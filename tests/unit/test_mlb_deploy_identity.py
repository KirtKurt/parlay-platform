from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import verify_mlb_deploy_identity as deploy_identity


STACK_NAME = "parlay-platform-test"
REGION = "us-east-1"
GIT_SHA = "a" * 40
TEMPLATE_SHA = "b" * 64
ARTIFACT_BUCKET = "parlay-platform-test-mlb-artifacts"


def _arn(role: str) -> str:
    return f"arn:aws:lambda:{REGION}:123456789012:function:{role}"


class FakeCloudFormation:
    def __init__(self) -> None:
        self.outputs = {
            deploy_identity.TRAINER_ARTIFACT_BUCKET_OUTPUT: ARTIFACT_BUCKET,
            deploy_identity.TRAINER_FUNCTION_ARN_OUTPUT: _arn("trainer"),
        }

    def describe_stacks(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"StackName": STACK_NAME}
        return {
            "Stacks": [
                {
                    "Outputs": [
                        {"OutputKey": key, "OutputValue": value}
                        for key, value in self.outputs.items()
                    ]
                }
            ]
        }

    def describe_stack_resource(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["StackName"] == STACK_NAME
        logical_id = kwargs["LogicalResourceId"]
        role = deploy_identity.FUNCTIONS[logical_id]
        return {"StackResourceDetail": {"PhysicalResourceId": f"physical-{role}"}}


class FakeLambda:
    def __init__(self) -> None:
        self.reserved_concurrency = None
        self.async_retry_policy = dict(deploy_identity.TRAINER_RETRY_POLICY)
        self.async_destination_config: dict[str, Any] = {}
        self.configurations = {}
        for logical_id, role in deploy_identity.FUNCTIONS.items():
            environment = {
                "INQSI_DEPLOY_GIT_SHA": GIT_SHA,
                "INQSI_DEPLOY_TEMPLATE_SHA256": TEMPLATE_SHA,
            }
            handler = f"mlb_{role}.lambda_handler"
            if role == "trainer":
                handler = deploy_identity.TRAINER_HANDLER
                environment.update(deploy_identity.TRAINER_EXPECTED_ENVIRONMENT)
                environment.update(
                    {
                        "MLB_ML_ARTIFACTS_BUCKET": ARTIFACT_BUCKET,
                        "SNAPSHOTS_TABLE": "snapshots",
                        "OUTCOMES_TABLE": "outcomes",
                        "ODDS_API_KEY": "must-not-appear-in-proof",
                    }
                )
            self.configurations[f"physical-{role}"] = {
                "FunctionArn": _arn(role),
                "Handler": handler,
                "Timeout": (
                    deploy_identity.TRAINER_TIMEOUT_SECONDS
                    if role == "trainer"
                    else 30
                ),
                "Runtime": "python3.11",
                "LastModified": "2026-07-21T00:00:00.000+0000",
                "CodeSha256": f"code-{logical_id}",
                "Version": "$LATEST",
                "Environment": {"Variables": environment},
            }

    def get_function_configuration(self, **kwargs: Any) -> dict[str, Any]:
        return copy.deepcopy(self.configurations[kwargs["FunctionName"]])

    def get_function_concurrency(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"FunctionName": "physical-trainer"}
        if self.reserved_concurrency is None:
            return {}
        return {"ReservedConcurrentExecutions": self.reserved_concurrency}

    def get_function_event_invoke_config(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "FunctionName": "physical-trainer",
            "Qualifier": "$LATEST",
        }
        response = dict(self.async_retry_policy)
        if self.async_destination_config:
            response["DestinationConfig"] = copy.deepcopy(
                self.async_destination_config
            )
        return response

    def list_functions(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"MaxItems": 50}
        return {
            "Functions": [
                {
                    "FunctionName": physical_name,
                    **copy.deepcopy(configuration),
                }
                for physical_name, configuration in self.configurations.items()
            ]
        }


class FakeEvents:
    def __init__(self) -> None:
        self.rules = {}
        for role, schedules in deploy_identity.EXPECTED_SCHEDULES.items():
            for index, schedule in enumerate(schedules):
                name = f"rule-{role}" if len(schedules) == 1 else f"rule-{role}-{index}"
                rule = {
                    "State": "ENABLED",
                    "ScheduleExpression": schedule,
                    "Arn": _arn(role),
                }
                if role == "trainer":
                    invocation = next(
                        item
                        for item in deploy_identity.TRAINER_EXPECTED_INVOCATIONS
                        if item["schedule"] == schedule
                    )
                    rule["Input"] = json.dumps(invocation["input"])
                self.rules[name] = rule

    def list_rule_names_by_target(self, **kwargs: Any) -> dict[str, Any]:
        target_arn = kwargs["TargetArn"]
        return {
            "RuleNames": [
                name
                for name, rule in self.rules.items()
                if rule["Arn"] == target_arn
            ]
        }

    def describe_rule(self, **kwargs: Any) -> dict[str, Any]:
        rule = self.rules[kwargs["Name"]]
        return {
            "Name": kwargs["Name"],
            "State": rule["State"],
            "ScheduleExpression": rule["ScheduleExpression"],
        }

    def list_targets_by_rule(self, **kwargs: Any) -> dict[str, Any]:
        rule_name = kwargs["Rule"]
        target = {"Arn": self.rules[rule_name]["Arn"]}
        if rule_name.startswith("rule-trainer-"):
            target.update(
                {
                    "Input": self.rules[rule_name]["Input"],
                    "RetryPolicy": dict(deploy_identity.TRAINER_RETRY_POLICY),
                }
            )
        return {"Targets": [target]}

    def list_rules(self, **kwargs: Any) -> dict[str, Any]:
        return {"Rules": [{"Name": name} for name in self.rules]}


class FakeS3:
    def __init__(self) -> None:
        self.versioning_status = "Enabled"
        self.checked_buckets: list[str] = []

    def get_bucket_versioning(self, **kwargs: Any) -> dict[str, str]:
        self.checked_buckets.append(kwargs["Bucket"])
        return {"Status": self.versioning_status}

    def get_bucket_encryption(self, **kwargs: Any) -> dict[str, Any]:
        self.checked_buckets.append(kwargs["Bucket"])
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        }
                    }
                ]
            }
        }

    def get_public_access_block(self, **kwargs: Any) -> dict[str, Any]:
        self.checked_buckets.append(kwargs["Bucket"])
        return {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            }
        }


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    clients = {
        "cloudformation": FakeCloudFormation(),
        "lambda": FakeLambda(),
        "events": FakeEvents(),
        "s3": FakeS3(),
    }

    def client(service_name: str, **kwargs: Any) -> Any:
        assert kwargs == {"region_name": REGION}
        return clients[service_name]

    monkeypatch.setattr(deploy_identity.boto3, "client", client)
    return clients


def _verify() -> dict[str, Any]:
    return deploy_identity.verify(
        stack_name=STACK_NAME,
        region=REGION,
        expected_git_sha=GIT_SHA,
        expected_template_sha256=TEMPLATE_SHA,
    )


def test_verifies_trainer_identity_configuration_schedule_and_bucket(aws) -> None:
    assert deploy_identity.EXPECTED_SCHEDULES["trainer"] == [
        "rate(6 hours)",
        "rate(15 minutes)",
    ]
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT["MLB_ML_EXPERIMENT_ID"] == (
        "mlb-v2-2026-07-21-future-prospective-r2"
    )
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT[
        "MLB_ML_RELEASE_CONTRACT_ID"
    ] == "mlb-v2-2026-07-21-future-prospective-r2"
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT[
        "MLB_ML_RELEASE_CUTOFF_UTC"
    ] == "2026-07-22T04:00:00+00:00"

    result = _verify()

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["functions"]["MLBMLTrainingFunction"]["configurationMatches"] is True
    assert result["trainerConfiguration"]["matches"] is True
    assert result["trainerConfiguration"]["executionConcurrencyStrategy"] == (
        "dynamodb_conditional_lease"
    )
    assert result["trainerConfiguration"]["executionLeaseSeconds"] == 960
    assert result["trainerConfiguration"]["reservedLambdaConcurrencyRequired"] is False
    assert result["trainerConfiguration"]["timeoutSeconds"] == 900
    assert result["trainerConfiguration"]["timeoutMatches"] is True
    assert result["trainerConfiguration"]["reservedConcurrentExecutions"] is None
    assert result["trainerConfiguration"]["reservedConcurrencyAbsent"] is True
    assert result["trainerConfiguration"]["functionDeadLetterConfigAbsent"] is True
    assert result["trainerConfiguration"]["asyncRetryPolicyMatches"] is True
    assert result["trainerConfiguration"]["asyncDestinationConfigAbsent"] is True
    assert result["trainerConfiguration"]["sqsFailureDestinationRequired"] is False
    assert result["trainerConfiguration"]["handler"] == deploy_identity.TRAINER_HANDLER
    assert result["trainerConfiguration"]["environment"]["MLB_ML_ARTIFACTS_BUCKET"] == ARTIFACT_BUCKET
    assert "ODDS_API_KEY" not in result["trainerConfiguration"]["environment"]
    assert result["schedules"]["trainer"]["exactMatch"] is True
    assert result["schedules"]["trainer"]["retryPolicyMatches"] is True
    assert result["schedules"]["trainer"]["deadLetterQueueAbsent"] is True
    assert result["schedules"]["trainer"]["deliveryPolicyMatches"] is True
    assert result["schedules"]["trainer"]["sqsFailureDestinationRequired"] is False
    assert result["schedules"]["trainer"]["invocationInputsMatch"] is True
    assert result["artifactBucket"] == {
        "stackOutputKey": deploy_identity.TRAINER_ARTIFACT_BUCKET_OUTPUT,
        "bucketName": ARTIFACT_BUCKET,
        "versioningStatus": "Enabled",
        "versioningEnabled": True,
        "serverSideEncryption": ["AES256"],
        "encrypted": True,
        "publicAccessBlocked": True,
    }
    assert aws["s3"].checked_buckets == [
        ARTIFACT_BUCKET,
        ARTIFACT_BUCKET,
        ARTIFACT_BUCKET,
    ]


def test_rejects_wrong_trainer_handler(aws) -> None:
    aws["lambda"].configurations["physical-trainer"]["Handler"] = "wrong.handler"

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["matches"] is False
    assert any(value.startswith("TRAINER_HANDLER_MISMATCH:") for value in result["blockers"])


def test_rejects_wrong_trainer_timeout(aws) -> None:
    aws["lambda"].configurations["physical-trainer"]["Timeout"] = 960

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["timeoutMatches"] is False
    assert any(
        value.startswith("TRAINER_TIMEOUT_MISMATCH:")
        for value in result["blockers"]
    )


@pytest.mark.parametrize("reserved_concurrency", (0, 1))
def test_rejects_present_trainer_reserved_concurrency(
    aws, reserved_concurrency
) -> None:
    aws["lambda"].reserved_concurrency = reserved_concurrency

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["reservedConcurrencyAbsent"] is False
    assert any(
        blocker.startswith("TRAINER_RESERVED_CONCURRENCY_PRESENT:")
        for blocker in result["blockers"]
    )


def test_fails_closed_when_reserved_concurrency_absence_cannot_be_read(aws) -> None:
    def denied(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("lambda concurrency inventory denied")

    aws["lambda"].get_function_concurrency = denied

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["matches"] is False
    assert any(
        blocker.startswith(
            "TRAINER_RESERVED_CONCURRENCY_ABSENCE_CHECK_FAILED:"
        )
        for blocker in result["blockers"]
    )


def test_rejects_wrong_lambda_async_retry_and_any_destination_config(aws) -> None:
    aws["lambda"].async_retry_policy["MaximumRetryAttempts"] = 0
    aws["lambda"].async_destination_config = {
        "OnSuccess": {
            "Destination": "arn:aws:lambda:us-east-1:123456789012:function:unexpected"
        }
    }

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["asyncRetryPolicyMatches"] is False
    assert result["trainerConfiguration"]["asyncDestinationConfigAbsent"] is False
    assert any(
        blocker.startswith("TRAINER_LAMBDA_ASYNC_RETRY_POLICY_MISMATCH:")
        for blocker in result["blockers"]
    )
    assert "TRAINER_LAMBDA_ASYNC_DESTINATION_CONFIG_PRESENT" in result[
        "blockers"
    ]


def test_rejects_trainer_function_dead_letter_config(aws) -> None:
    aws["lambda"].configurations["physical-trainer"]["DeadLetterConfig"] = {
        "TargetArn": "arn:aws:sqs:us-east-1:123456789012:unexpected"
    }

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["functionDeadLetterConfigAbsent"] is False
    assert "TRAINER_FUNCTION_DEAD_LETTER_CONFIG_PRESENT" in result["blockers"]


def test_fails_closed_when_lambda_async_retry_config_cannot_be_read(aws) -> None:
    def denied(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("lambda event invoke config inventory denied")

    aws["lambda"].get_function_event_invoke_config = denied

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["asyncRetryPolicyMatches"] is False
    assert any(
        blocker.startswith("TRAINER_LAMBDA_ASYNC_RETRY_POLICY_CHECK_FAILED:")
        for blocker in result["blockers"]
    )


def test_rejects_legacy_trainer_sqs_stack_output(aws) -> None:
    aws["cloudformation"].outputs[
        deploy_identity.TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT
    ] = ""

    result = _verify()

    assert result["ok"] is False
    assert (
        "TRAINER_SQS_FALLBACK_STACK_OUTPUT_PRESENT:"
        f"{deploy_identity.TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT}"
    ) in result["blockers"]


@pytest.mark.parametrize(
    ("key", "value", "blocker_prefix"),
    (
        (
            "MLB_ML_RELEASE_CONTRACT_ID",
            "stale-contract",
            "TRAINER_ENVIRONMENT_MISMATCH:MLB_ML_RELEASE_CONTRACT_ID:",
        ),
        (
            "MLB_ML_EXECUTION_LEASE_SECONDS",
            "1200",
            "TRAINER_ENVIRONMENT_MISMATCH:MLB_ML_EXECUTION_LEASE_SECONDS:",
        ),
        (
            "SNAPSHOTS_TABLE",
            "",
            "TRAINER_ENVIRONMENT_MISSING:SNAPSHOTS_TABLE",
        ),
        (
            "MLB_ML_ARTIFACTS_BUCKET",
            "wrong-bucket",
            "TRAINER_ARTIFACT_BUCKET_MISMATCH:",
        ),
    ),
)
def test_rejects_wrong_or_missing_trainer_environment(
    aws, key: str, value: str, blocker_prefix: str
) -> None:
    environment = aws["lambda"].configurations["physical-trainer"]["Environment"]["Variables"]
    environment[key] = value

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["matches"] is False
    assert any(item.startswith(blocker_prefix) for item in result["blockers"])


def test_rejects_wrong_trainer_schedule(aws) -> None:
    aws["events"].rules["rule-trainer-0"]["ScheduleExpression"] = "rate(12 hours)"

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["trainer"]["exactMatch"] is False
    assert any(item.startswith("SCHEDULE_MISMATCH:trainer:") for item in result["blockers"])


def test_rejects_artifact_bucket_without_enabled_versioning(aws) -> None:
    aws["s3"].versioning_status = "Suspended"

    result = _verify()

    assert result["ok"] is False
    assert result["artifactBucket"]["versioningEnabled"] is False
    assert any(
        item.startswith("TRAINER_ARTIFACT_BUCKET_VERSIONING_NOT_ENABLED:")
        for item in result["blockers"]
    )


def test_rejects_trainer_schedule_without_retry_policy(aws) -> None:
    original = aws["events"].list_targets_by_rule

    def without_delivery_config(**kwargs: Any) -> dict[str, Any]:
        response = original(**kwargs)
        if kwargs["Rule"].startswith("rule-trainer-"):
            response["Targets"][0].pop("RetryPolicy", None)
        return response

    aws["events"].list_targets_by_rule = without_delivery_config

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["trainer"]["retryPolicyMatches"] is False
    assert "TRAINER_EVENTBRIDGE_RETRY_POLICY_MISMATCH" in result["blockers"]


def test_rejects_unexpected_trainer_dead_letter_destination(aws) -> None:
    original = aws["events"].list_targets_by_rule

    def with_dead_letter(**kwargs: Any) -> dict[str, Any]:
        response = original(**kwargs)
        if kwargs["Rule"].startswith("rule-trainer-"):
            response["Targets"][0]["DeadLetterConfig"] = {
                "Arn": "arn:aws:sqs:us-east-1:123456789012:unexpected"
            }
        return response

    aws["events"].list_targets_by_rule = with_dead_letter

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["trainer"]["deadLetterQueueAbsent"] is False
    assert "TRAINER_EVENTBRIDGE_FAILURE_DESTINATION_PRESENT" in result[
        "blockers"
    ]


def test_template_preserves_retries_without_requiring_sqs_create() -> None:
    template = (
        Path(__file__).resolve().parents[2] / "template.yaml"
    ).read_text(encoding="utf-8")

    assert "AWS::SQS::Queue" not in template
    assert "MLBMLTrainingDeadLetterQueue" not in template
    assert "sqs:SendMessage" not in template
    assert template.count("MaximumEventAgeInSeconds: 21600") >= 3
    assert template.count("MaximumRetryAttempts: 2") >= 3


def test_rejects_trainer_schedule_with_swapped_invocation_modes(aws) -> None:
    first = aws["events"].rules["rule-trainer-0"]
    second = aws["events"].rules["rule-trainer-1"]
    first["Input"], second["Input"] = second["Input"], first["Input"]

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["trainer"]["invocationInputsMatch"] is False
    assert "TRAINER_EVENTBRIDGE_INVOCATION_INPUT_MISMATCH" in result["blockers"]


def test_deploy_initializes_both_trainer_modes_before_status_acceptance() -> None:
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")
    training_payload = (
        "--payload "
        "'{\"sport\":\"mlb\",\"mode\":\"scheduled\","
        "\"run\":\"aws_native_fixed_prospective_shadow_training\"}'"
    )
    capture_payload = (
        "--payload "
        "'{\"sport\":\"mlb\",\"mode\":\"selection_capture\","
        "\"run\":\"aws_native_prospective_selection_capture\"}'"
    )
    status_payload = "--payload '{\"mode\":\"status\"}'"

    assert workflow.index(training_payload) < workflow.index(capture_payload)
    assert workflow.index(capture_payload) < workflow.index(status_payload)
    assert workflow.count("--cli-read-timeout 1000") >= 3
    assert "trainingHealth" in workflow
    assert "selectionCaptureHealth" in workflow
    assert "deploymentIdentityMatches" in workflow


def test_rejects_enabled_legacy_mlb_pull_function_outside_current_stack(aws) -> None:
    legacy_arn = _arn("MLBHotPullRecoveryFunction")
    aws["lambda"].configurations["legacy-mlb-hot-pull"] = {
        "FunctionArn": legacy_arn,
        "Handler": "mlb_manual_pull.lambda_handler",
        "Runtime": "python3.11",
        "Environment": {"Variables": {}},
    }
    aws["events"].rules["nightly-recovery"] = {
        "State": "ENABLED",
        "ScheduleExpression": "cron(0 6 * * ? *)",
        "Arn": legacy_arn,
    }

    result = _verify()

    assert result["ok"] is False
    assert "ENABLED_ALTERNATE_MLB_PULL_OR_TRAINING_WRITERS" in result["blockers"]
    alternates = result["alternateWriterAuthority"]["enabledAlternateRules"]
    assert [item["name"] for item in alternates] == ["nightly-recovery"]
    assert alternates[0]["targets"][0]["arn"] == legacy_arn


def test_rejects_enabled_known_legacy_rule_with_noncanonical_target(aws) -> None:
    target_arn = "arn:aws:states:us-east-1:123456789012:stateMachine:writer"
    aws["events"].rules["MLBBasePullLegacy"] = {
        "State": "ENABLED",
        "ScheduleExpression": "rate(15 minutes)",
        "Arn": target_arn,
    }

    result = _verify()

    assert result["ok"] is False
    alternate = result["alternateWriterAuthority"]["enabledAlternateRules"][0]
    assert alternate["name"] == "MLBBasePullLegacy"
    assert alternate["legacyNamedRule"] is True


def test_ignores_enabled_unrelated_sport_training_writer(aws) -> None:
    soccer_arn = _arn("soccer-training")
    aws["lambda"].configurations["soccer-training"] = {
        "FunctionArn": soccer_arn,
        "Handler": "soccer_ml_training.lambda_handler",
        "Runtime": "python3.11",
        "Environment": {"Variables": {}},
    }
    aws["events"].rules["soccer-training-every-six-hours"] = {
        "State": "ENABLED",
        "ScheduleExpression": "rate(6 hours)",
        "Arn": soccer_arn,
    }

    result = _verify()

    assert result["ok"] is True
    assert result["alternateWriterAuthority"]["enabledAlternateRules"] == []


def test_fails_closed_when_regional_writer_discovery_fails(aws) -> None:
    def unavailable(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("lambda inventory denied")

    aws["lambda"].list_functions = unavailable

    result = _verify()

    assert result["ok"] is False
    assert result["alternateWriterAuthority"]["scanComplete"] is False
    assert any(
        blocker.startswith("MLB_ALTERNATE_WRITER_DISCOVERY_FAILED:")
        for blocker in result["blockers"]
    )
