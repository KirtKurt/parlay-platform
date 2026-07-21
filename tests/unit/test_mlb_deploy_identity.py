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
OMIT_DESTINATION_CONFIG = object()


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
        self.async_retry_policies = {
            role: dict(policy)
            for role, policy in deploy_identity.FUNCTION_ASYNC_RETRY_POLICIES.items()
        }
        # Preserve the focused trainer mutation interface used by older tests.
        self.async_retry_policy = self.async_retry_policies["trainer"]
        self.async_destination_config: Any = {
            "OnSuccess": {},
            "OnFailure": {},
        }
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
            if role == "ingest":
                environment.update(
                    {
                        "BBS_API_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:bbs",
                        "BBS_SHADOW_CAPTURE_ENABLED": "true",
                        "BBS_SHADOW_S3_BUCKET": ARTIFACT_BUCKET,
                        "BBS_SHADOW_SCHEMA_VERSION": "MLB-BBS-SHADOW-v2-canonical-bound-raw-only",
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
        assert kwargs["Qualifier"] == "$LATEST"
        role = str(kwargs["FunctionName"]).removeprefix("physical-")
        response = dict(self.async_retry_policies[role])
        if self.async_destination_config is not OMIT_DESTINATION_CONFIG:
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
                expected_invocations = deploy_identity.SCHEDULE_EXPECTED_INVOCATIONS.get(
                    role
                )
                if expected_invocations is not None:
                    invocation = next(
                        item
                        for item in expected_invocations
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
        role = (
            rule_name.split("-", 1)[1].split("-", 1)[0]
            if "-" in rule_name
            else ""
        )
        expected_policies = deploy_identity.SCHEDULE_RETRY_POLICIES.get(role)
        if expected_policies is not None:
            schedule = self.rules[rule_name]["ScheduleExpression"]
            target.update(
                {
                    "RetryPolicy": dict(
                        expected_policies.get(
                            schedule,
                            next(iter(expected_policies.values())),
                        )
                    ),
                }
            )
        if "Input" in self.rules[rule_name]:
            target["Input"] = self.rules[rule_name]["Input"]
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
        "cron(11 1/6 * * ? *)",
        "cron(4/15 * * * ? *)",
    ]
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT["MLB_ML_EXPERIMENT_ID"] == (
        "mlb-v2-2026-07-22-future-prospective-r3"
    )
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT[
        "MLB_ML_RELEASE_CONTRACT_ID"
    ] == "mlb-v2-2026-07-22-future-prospective-r3"
    assert deploy_identity.TRAINER_EXPECTED_ENVIRONMENT[
        "MLB_ML_RELEASE_CUTOFF_UTC"
    ] == "2026-07-22T04:00:00+00:00"

    result = _verify()

    assert result["ok"] is True
    assert result["blockers"] == []
    assert result["providerCredentialBoundary"] == {
        "provider": "Big Balls Sports Data",
        "exactGithubSecretName": "BBS_API_KEY",
        "runtimeSecretArnEnvironment": "BBS_API_SECRET_ARN",
        "consumerRole": "ingest",
        "secretArnPresentOnIngest": True,
        "shadowEnvironmentMatches": True,
        "plaintextKeyEnvironmentAbsent": True,
        "retiredProviderEnvironmentAbsent": True,
        "otherCanonicalFunctionsWithoutBbsAuthority": True,
    }
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
    assert result["trainerConfiguration"]["asyncDestinationConfig"] == {
        "OnSuccess": {},
        "OnFailure": {},
    }
    assert result["trainerConfiguration"]["asyncDestinationConfigPresent"] is True
    assert result["trainerConfiguration"]["asyncDestinationConfigValid"] is True
    assert result["trainerConfiguration"]["configuredAsyncDestinations"] == {}
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
    for role in ("ingest", "lock", "trainer", "settlement", "soccer", "autopsy"):
        assert result["schedules"][role]["exactMatch"] is True
        assert result["schedules"][role]["targetTopologyMatches"] is True
        assert result["schedules"][role]["retryPolicyMatches"] is True
        assert result["schedules"][role]["deadLetterQueueAbsent"] is True
        assert result["schedules"][role]["deliveryPolicyMatches"] is True
        assert result["schedules"][role]["invocationInputsMatch"] is True
    assert result["schedules"]["verifier"]["expectedSchedules"] == []
    assert result["schedules"]["verifier"]["enabledRules"] == []
    assert result["schedules"]["verifier"]["exactMatch"] is True
    assert result["schedules"]["verifier"]["targetTopologyMatches"] is True
    for logical_id, role in deploy_identity.FUNCTIONS.items():
        proof = result["functions"][logical_id]["asyncDeliveryPolicy"]
        expected = deploy_identity.FUNCTION_ASYNC_RETRY_POLICIES.get(role)
        if expected is None:
            assert proof["retryPolicyMatches"] is None
            continue
        assert proof["expectedRetryPolicy"] == expected
        assert proof["actualRetryPolicy"] == expected
        assert proof["retryPolicyMatches"] is True
        assert proof["destinationConfigAbsent"] is True
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


def test_rejects_bbs_credential_authority_on_public_read_lambda(aws) -> None:
    environment = aws["lambda"].configurations["physical-read"]["Environment"]["Variables"]
    environment["BBS_API_SECRET_ARN"] = "arn:forbidden"

    result = _verify()

    assert result["ok"] is False
    assert result["providerCredentialBoundary"]["otherCanonicalFunctionsWithoutBbsAuthority"] is False
    assert any(
        blocker.startswith("BBS_AUTHORITY_LEAKED_TO_READ:")
        for blocker in result["blockers"]
    )


def test_rejects_plaintext_or_retired_provider_environment_drift(aws) -> None:
    ingest = aws["lambda"].configurations["physical-ingest"]["Environment"]["Variables"]
    ingest["BBS_API_KEY"] = "must-not-be-a-lambda-environment-value"
    ingest["SPORTSDATAIO_API_KEY"] = "retired"

    result = _verify()

    assert result["ok"] is False
    assert result["providerCredentialBoundary"]["plaintextKeyEnvironmentAbsent"] is False
    assert result["providerCredentialBoundary"]["retiredProviderEnvironmentAbsent"] is False
    assert any(
        blocker.startswith("BBS_PLAINTEXT_KEY_ENVIRONMENT_PRESENT:")
        for blocker in result["blockers"]
    )
    assert any(
        blocker.startswith("RETIRED_PROVIDER_ENVIRONMENT_PRESENT:")
        for blocker in result["blockers"]
    )


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


@pytest.mark.parametrize(
    "destination_config",
    (
        deploy_identity._MISSING_ASYNC_DESTINATION_CONFIG,
        {},
        {"OnSuccess": {}},
        {"OnFailure": {}},
        {"OnSuccess": {}, "OnFailure": {}},
    ),
)
def test_normalizes_absent_lambda_async_destinations(destination_config) -> None:
    assert deploy_identity._normalize_async_destination_config(
        destination_config
    ) == {}


@pytest.mark.parametrize("outcome", ("OnSuccess", "OnFailure"))
def test_rejects_wrong_lambda_async_retry_and_configured_destination(
    aws, outcome
) -> None:
    aws["lambda"].async_retry_policy["MaximumRetryAttempts"] = 2
    aws["lambda"].async_destination_config = {
        outcome: {
            "Destination": "arn:aws:lambda:us-east-1:123456789012:function:unexpected"
        }
    }

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["asyncRetryPolicyMatches"] is False
    assert result["trainerConfiguration"]["asyncDestinationConfigValid"] is True
    assert result["trainerConfiguration"]["asyncDestinationConfigAbsent"] is False
    assert result["trainerConfiguration"]["configuredAsyncDestinations"] == {
        outcome: {
            "Destination": (
                "arn:aws:lambda:us-east-1:123456789012:function:unexpected"
            )
        }
    }
    assert any(
        blocker.startswith("TRAINER_LAMBDA_ASYNC_RETRY_POLICY_MISMATCH:")
        for blocker in result["blockers"]
    )
    assert "TRAINER_LAMBDA_ASYNC_DESTINATION_CONFIG_PRESENT" in result[
        "blockers"
    ]


@pytest.mark.parametrize(
    "destination_config",
    (
        [],
        "",
        0,
        None,
        {"OnFailure": "invalid"},
        {"OnSuccess": None},
        {"OnFailure": []},
        {"OnSuccess": {"Destination": 123}},
        {"OnSuccess": {"Destination": ""}},
        {"Unexpected": {}},
        {
            "OnFailure": {
                "Destination": "arn:aws:sqs:us-east-1:123456789012:unexpected",
                "Extra": True,
            }
        },
    ),
)
def test_fails_closed_for_malformed_lambda_async_destination_config(
    aws, destination_config
) -> None:
    aws["lambda"].async_destination_config = destination_config

    result = _verify()

    assert result["ok"] is False
    assert result["trainerConfiguration"]["asyncDestinationConfigValid"] is False
    assert result["trainerConfiguration"]["asyncDestinationConfigAbsent"] is False
    assert any(
        blocker.startswith(
            "TRAINER_LAMBDA_ASYNC_DESTINATION_CONFIG_INVALID:"
        )
        for blocker in result["blockers"]
    )


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


@pytest.mark.parametrize("role", ("ingest", "lock", "settlement", "soccer", "autopsy"))
def test_rejects_wrong_nontrainer_schedule_input(aws, role) -> None:
    rule_name = next(
        name for name in aws["events"].rules if name.startswith(f"rule-{role}")
    )
    aws["events"].rules[rule_name]["Input"] = json.dumps({"wrong": role})

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"][role]["invocationInputsMatch"] is False
    assert f"EVENTBRIDGE_INVOCATION_INPUT_MISMATCH:{role}" in result["blockers"]


def test_rejects_wrong_nontrainer_lambda_async_retry_policy(aws) -> None:
    aws["lambda"].async_retry_policies["soccer"]["MaximumRetryAttempts"] = 2

    result = _verify()

    proof = result["functions"]["SoccerSchedulerFunction"]["asyncDeliveryPolicy"]
    assert result["ok"] is False
    assert proof["retryPolicyMatches"] is False
    assert any(
        blocker.startswith("LAMBDA_ASYNC:SOCCER_ASYNC_RETRY_POLICY_MISMATCH:")
        for blocker in result["blockers"]
    )


def test_rejects_duplicate_same_arn_target(aws) -> None:
    original = aws["events"].list_targets_by_rule

    def duplicate_target(**kwargs: Any) -> dict[str, Any]:
        response = original(**kwargs)
        if kwargs["Rule"] == "rule-lock":
            response["Targets"].append(copy.deepcopy(response["Targets"][0]))
        return response

    aws["events"].list_targets_by_rule = duplicate_target

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["lock"]["targetTopologyMatches"] is False
    assert "EVENTBRIDGE_TARGET_TOPOLOGY_MISMATCH:lock" in result["blockers"]


def test_rejects_extra_unrelated_target_on_canonical_rule(aws) -> None:
    original = aws["events"].list_targets_by_rule

    def extra_target(**kwargs: Any) -> dict[str, Any]:
        response = original(**kwargs)
        if kwargs["Rule"] == "rule-settlement":
            response["Targets"].append({"Arn": _arn("unexpected")})
        return response

    aws["events"].list_targets_by_rule = extra_target

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["settlement"]["targetTopologyMatches"] is False
    assert "EVENTBRIDGE_TARGET_TOPOLOGY_MISMATCH:settlement" in result["blockers"]


def test_rejects_qualified_alias_target_on_canonical_rule(aws) -> None:
    aws["events"].rules["rule-lock"]["Arn"] = _arn("lock") + ":live"

    result = _verify()

    assert result["ok"] is False
    assert result["schedules"]["lock"]["exactMatch"] is True
    assert result["schedules"]["lock"]["targetTopologyMatches"] is False
    assert "EVENTBRIDGE_TARGET_TOPOLOGY_MISMATCH:lock" in result["blockers"]


def test_rejects_extra_qualified_alias_schedule_for_canonical_function(aws) -> None:
    aws["events"].rules["rule-lock-alias"] = {
        "State": "ENABLED",
        "ScheduleExpression": "rate(2 minutes)",
        "Arn": _arn("lock") + ":live",
        "Input": json.dumps(
            {
                "sport": "mlb",
                "run": "aws_immutable_t45_lock_v1",
            }
        ),
    }

    result = _verify()

    assert result["ok"] is False
    assert sorted(
        rule["name"] for rule in result["schedules"]["lock"]["enabledRules"]
    ) == ["rule-lock", "rule-lock-alias"]
    assert result["schedules"]["lock"]["exactMatch"] is False
    assert result["schedules"]["lock"]["targetTopologyMatches"] is False
    assert "EVENTBRIDGE_TARGET_TOPOLOGY_MISMATCH:lock" in result["blockers"]


def test_disabled_verifier_rule_remains_allowed_but_enabled_rule_is_rejected(aws) -> None:
    aws["events"].rules["rule-verifier-disabled"] = {
        "State": "DISABLED",
        "ScheduleExpression": "cron(2/5 * * * ? *)",
        "Arn": _arn("verifier") + ":diagnostic",
        "Input": json.dumps(
            {
                "sport": "mlb",
                "mode": "continuous",
                "run": "aws_production_verifier_5m",
            }
        ),
    }

    disabled = _verify()

    assert disabled["ok"] is True
    assert disabled["schedules"]["verifier"]["enabledRules"] == []
    assert disabled["schedules"]["verifier"]["targetTopologyMatches"] is True

    aws["events"].rules["rule-verifier-disabled"]["State"] = "ENABLED"
    enabled = _verify()

    assert enabled["ok"] is False
    assert enabled["schedules"]["verifier"]["targetTopologyMatches"] is False
    assert any(
        blocker.startswith("SCHEDULE_MISMATCH:verifier:")
        for blocker in enabled["blockers"]
    )


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
    assert "ReservedConcurrentExecutions:" not in template
    assert "MLB_ML_EXECUTION_LEASE_SECONDS: '960'" in template
    assert template.count("MaximumEventAgeInSeconds: 3600") >= 2
    assert template.count("MaximumEventAgeInSeconds: 300") >= 2
    assert template.count("MaximumEventAgeInSeconds: 60") >= 2
    assert template.count("MaximumRetryAttempts: 0") >= 8

    trainer = template.split("  MLBMLTrainingFunction:\n", 1)[1].split(
        "\n  SoccerSchedulerFunction:\n", 1
    )[0]
    assert trainer.count("MaximumEventAgeInSeconds: 3600") == 1
    assert trainer.count("MaximumEventAgeInSeconds: 300") == 2


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
        "'{\"sport\":\"mlb\",\"mode\":\"scheduled\","
        "\"run\":\"aws_native_fixed_prospective_shadow_training\"}'"
    )
    capture_payload = (
        "'{\"sport\":\"mlb\",\"mode\":\"selection_capture\","
        "\"run\":\"aws_native_prospective_selection_capture\"}'"
    )
    status_payload = "'{\"mode\":\"status\"}'"

    assert workflow.index(training_payload) < workflow.index(capture_payload)
    assert workflow.index(capture_payload) < workflow.index(status_payload)
    assert 'AWS_MAX_ATTEMPTS: "1"' in workflow
    assert workflow.count("python scripts/invoke_mlb_trainer_with_retry.py") == 3
    assert "invoke_with_capacity_retry" not in workflow
    assert "Prove shared Lambda capacity recovered before trainer initialization" in workflow
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
