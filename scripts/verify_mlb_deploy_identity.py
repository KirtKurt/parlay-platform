from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

try:
    from scripts.mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        MAX_COMPRESSED_ARTIFACT_BYTES,
        lambda_code_sha256,
        zip_content_manifest,
    )
except ModuleNotFoundError:
    from mlb_lambda_artifact_identity import (
        MANIFEST_SCHEMA_VERSION,
        MAX_COMPRESSED_ARTIFACT_BYTES,
        lambda_code_sha256,
        zip_content_manifest,
    )


FUNCTIONS = {
    "MLBAuditedPullFunction": "ingest",
    "MLBDailyPickLockFunction": "lock",
    "MLBMLTrainingFunction": "trainer",
    "MLBProductionVerifierFunction": "verifier",
    "MLBV3ReadFunction": "read",
    "MLBResultsSchedulerFunction": "settlement",
    "SoccerSchedulerFunction": "soccer",
    "InqsiAutopsySchedulerFunction": "autopsy",
}

EXPECTED_SCHEDULES = {
    "read": [],
    "ingest": ["cron(0/15 * * * ? *)"],
    "lock": ["rate(1 minute)"],
    "trainer": ["cron(11 1/6 * * ? *)", "cron(4/15 * * * ? *)"],
    # The old verifier is intentionally schedule-disabled until its runtime is
    # persisted-summary-only. It remains directly invocable for diagnostics.
    "verifier": [],
    "settlement": ["cron(6/15 * * * ? *)"],
    "soccer": ["cron(9/15 * * * ? *)"],
    "autopsy": ["cron(13 6 * * ? *)"],
}

TRAINER_EXPECTED_INVOCATIONS = (
    {
        "schedule": "cron(11 1/6 * * ? *)",
        "input": {
            "sport": "mlb",
            "mode": "scheduled",
            "run": "aws_native_fixed_prospective_shadow_training",
        },
    },
    {
        "schedule": "cron(4/15 * * * ? *)",
        "input": {
            "sport": "mlb",
            "mode": "selection_capture",
            "run": "aws_native_prospective_selection_capture",
        },
    },
)

SCHEDULE_EXPECTED_INVOCATIONS = {
    "ingest": (
        {
            "schedule": "cron(0/15 * * * ? *)",
            "input": {
                "sport": "mlb",
                "t": "HOT",
                "run": "hot_pull_audited",
                "days_ahead": 0,
            },
        },
    ),
    "lock": (
        {
            "schedule": "rate(1 minute)",
            "input": {
                "sport": "mlb",
                "run": "daily_lock_check",
                "auto_ingest": False,
            },
        },
    ),
    "trainer": TRAINER_EXPECTED_INVOCATIONS,
    "settlement": (
        {
            "schedule": "cron(6/15 * * * ? *)",
            "input": {
                "sport": "mlb",
                "days_from": 3,
                "run": "results_pull_15m",
            },
        },
    ),
    "soccer": (
        {
            "schedule": "cron(9/15 * * * ? *)",
            "input": {
                "sport": "soccer",
                "t": "HOT",
                "run": "hot_pull_audited",
            },
        },
    ),
    "autopsy": (
        {
            "schedule": "cron(13 6 * * ? *)",
            "input": {
                "sport_key": "all",
                "mode": "grade",
                "run": "nightly_autopsy_1am_et",
            },
        },
    ),
}

TRAINER_HANDLER = "mlb_ml_aws_training_v1.lambda_handler"
TRAINER_TIMEOUT_SECONDS = 900
TRAINER_EXECUTION_CONCURRENCY_STRATEGY = "dynamodb_conditional_lease"
TRAINER_EXECUTION_LEASE_SECONDS = "960"
TRAINER_ARTIFACT_BUCKET_OUTPUT = "MLBMLArtifactsBucketName"
TRAINER_FUNCTION_ARN_OUTPUT = "MLBMLTrainingFunctionArn"
TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT = "MLBMLTrainingDeadLetterQueueArn"
LOCK_HANDLER = "mlb_daily_pick_lock_protected.lambda_handler"
LOCK_TIMEOUT_SECONDS = 300
LOCK_EXECUTION_CONCURRENCY_STRATEGY = "dynamodb_conditional_lease"
LOCK_EXECUTION_LEASE_SECONDS = "360"
LOCK_EXECUTION_LEASE_SAFETY_MARGIN_SECONDS = 60
LOCK_REQUIRED_ENVIRONMENT = ("SNAPSHOTS_TABLE",)
TRAINER_RETRY_POLICY = {
    "MaximumEventAgeInSeconds": 300,
    "MaximumRetryAttempts": 0,
}
TRAINER_EVENTBRIDGE_RETRY_POLICY = {
    "MaximumEventAgeInSeconds": 3600,
    "MaximumRetryAttempts": 0,
}
SCHEDULE_RETRY_POLICIES = {
    "ingest": {
        "cron(0/15 * * * ? *)": {
            "MaximumEventAgeInSeconds": 300,
            "MaximumRetryAttempts": 1,
        },
    },
    "lock": {
        "rate(1 minute)": {
            "MaximumEventAgeInSeconds": 60,
            "MaximumRetryAttempts": 0,
        },
    },
    "trainer": {
        "cron(11 1/6 * * ? *)": dict(TRAINER_EVENTBRIDGE_RETRY_POLICY),
        "cron(4/15 * * * ? *)": {
            "MaximumEventAgeInSeconds": 300,
            "MaximumRetryAttempts": 0,
        },
    },
    "settlement": {
        "cron(6/15 * * * ? *)": {
            "MaximumEventAgeInSeconds": 300,
            "MaximumRetryAttempts": 0,
        },
    },
    "soccer": {
        "cron(9/15 * * * ? *)": {
            "MaximumEventAgeInSeconds": 300,
            "MaximumRetryAttempts": 0,
        },
    },
    "autopsy": {
        "cron(13 6 * * ? *)": {
            "MaximumEventAgeInSeconds": 3600,
            "MaximumRetryAttempts": 0,
        },
    },
}
FUNCTION_ASYNC_RETRY_POLICIES = {
    "ingest": {
        "MaximumEventAgeInSeconds": 300,
        "MaximumRetryAttempts": 1,
    },
    "lock": {
        "MaximumEventAgeInSeconds": 60,
        "MaximumRetryAttempts": 0,
    },
    "trainer": dict(TRAINER_RETRY_POLICY),
    "verifier": {
        "MaximumEventAgeInSeconds": 300,
        "MaximumRetryAttempts": 0,
    },
    "settlement": {
        "MaximumEventAgeInSeconds": 300,
        "MaximumRetryAttempts": 0,
    },
    "soccer": {
        "MaximumEventAgeInSeconds": 300,
        "MaximumRetryAttempts": 0,
    },
    "autopsy": {
        "MaximumEventAgeInSeconds": 3600,
        "MaximumRetryAttempts": 0,
    },
}
TRAINER_EXPECTED_ENVIRONMENT = {
    "MLB_ML_EXPERIMENT_ID": "mlb-v2-2026-07-24-future-prospective-r4",
    "MLB_ML_RELEASE_CONTRACT_ID": "mlb-v2-2026-07-24-future-prospective-r4",
    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-07-24T04:00:00+00:00",
    "MLB_ML_FEATURE_VECTOR_VERSION": (
        "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness"
    ),
    "MLB_ML_EXECUTION_LEASE_SECONDS": TRAINER_EXECUTION_LEASE_SECONDS,
    "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false",
    "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED": "false",
    "INQSI_MLB_ML_AUTO_PROMOTE": "false",
}
LOCK_TIMEOUT_SECONDS = 300
LOCK_EXECUTION_LEASE_SECONDS = "360"
LOCK_EXPECTED_ENVIRONMENT = {
    "MLB_LOCK_EXECUTION_LEASE_SECONDS": LOCK_EXECUTION_LEASE_SECONDS,
}
TRAINER_REQUIRED_ENVIRONMENT = (
    "MLB_ML_ARTIFACTS_BUCKET",
    "SNAPSHOTS_TABLE",
    "OUTCOMES_TABLE",
)

BBS_EXPECTED_INGEST_ENVIRONMENT = {
    "BBS_SHADOW_CAPTURE_ENABLED": "true",
    "BBS_SHADOW_SCHEMA_VERSION": (
        "MLB-BBS-SHADOW-v2-canonical-bound-raw-only"
    ),
}
BBS_SECRET_ARN_ENVIRONMENT = "BBS_API_SECRET_ARN"
BBS_FORBIDDEN_PLAINTEXT_ENVIRONMENT = "BBS_API_KEY"
RETIRED_PROVIDER_ENVIRONMENT = (
    "SPORTSDATAIO_API_KEY",
    "SPORTSDATAIO_BASE_URL",
    "SPORTSDATAIO_MLB_GAMES_ENDPOINT",
    "SPORTSDATAIO_MLB_PBP_ENDPOINT",
)
_MISSING_ASYNC_DESTINATION_CONFIG = object()
ENABLED_RULE_STATES = {
    "ENABLED",
    "ENABLED_WITH_ALL_CLOUDTRAIL_MANAGEMENT_EVENTS",
}

LEGACY_TOKENS = (
    "MLBBASEPULL",
    "MLBT2",
    "MLBT3",
    "MLBT4",
    "MLBHOTKICKOFF",
    "MLBHOTPULLRECOVERY",
    "INQSIMLBV1CORE",
)

MLB_WRITER_TOKENS = (
    "PULL",
    "INGEST",
    "TRAIN",
    "LEARN",
    "OPTIM",
    "CHAMPION",
    "CHALLENGER",
)


def _normalize_async_destination_config(
    value: Any,
) -> Dict[str, Dict[str, str]]:
    """Normalize Lambda destinations while rejecting unknown shapes."""

    if value is _MISSING_ASYNC_DESTINATION_CONFIG:
        return {}
    if not isinstance(value, dict):
        raise ValueError("DestinationConfig must be an object")

    allowed_outcomes = {"OnSuccess", "OnFailure"}
    unexpected_outcomes = sorted(set(value) - allowed_outcomes)
    if unexpected_outcomes:
        raise ValueError(
            f"DestinationConfig has unknown outcomes: {unexpected_outcomes}"
        )

    configured: Dict[str, Dict[str, str]] = {}
    for outcome, raw_destination in value.items():
        if not isinstance(raw_destination, dict):
            raise ValueError(f"DestinationConfig.{outcome} must be an object")
        if not raw_destination:
            continue
        if set(raw_destination) != {"Destination"}:
            raise ValueError(
                f"DestinationConfig.{outcome} has invalid fields"
            )
        destination = raw_destination.get("Destination")
        if not isinstance(destination, str) or not destination.strip():
            raise ValueError(
                f"DestinationConfig.{outcome}.Destination must be a non-empty string"
            )
        configured[outcome] = {"Destination": destination}
    return configured


def _rule_names_for_target(events: Any, target_arn: str) -> List[str]:
    names: List[str] = []
    token = None
    while True:
        args: Dict[str, Any] = {"TargetArn": target_arn, "Limit": 100}
        if token:
            args["NextToken"] = token
        response = events.list_rule_names_by_target(**args)
        names.extend(str(name) for name in response.get("RuleNames") or [])
        token = response.get("NextToken")
        if not token:
            return sorted(set(names))


def _all_rule_names(events: Any) -> List[str]:
    names: List[str] = []
    token = None
    while True:
        args: Dict[str, Any] = {"Limit": 100}
        if token:
            args["NextToken"] = token
        response = events.list_rules(**args)
        names.extend(str(rule.get("Name")) for rule in response.get("Rules") or [] if rule.get("Name"))
        token = response.get("NextToken")
        if not token:
            return sorted(set(names))


def _all_lambda_functions(lambdas: Any) -> List[Dict[str, Any]]:
    functions: List[Dict[str, Any]] = []
    marker = None
    while True:
        args: Dict[str, Any] = {"MaxItems": 50}
        if marker:
            args["Marker"] = marker
        response = lambdas.list_functions(**args)
        functions.extend(
            function
            for function in (response.get("Functions") or [])
            if isinstance(function, dict)
        )
        marker = response.get("NextMarker")
        if not marker:
            return functions


def _targets_for_rule(events: Any, rule_name: str) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    token = None
    while True:
        args: Dict[str, Any] = {"Rule": rule_name, "Limit": 100}
        if token:
            args["NextToken"] = token
        response = events.list_targets_by_rule(**args)
        targets.extend(
            target
            for target in (response.get("Targets") or [])
            if isinstance(target, dict)
        )
        token = response.get("NextToken")
        if not token:
            return targets


def _authority_text(*values: Any) -> str:
    return "".join(
        character
        for value in values
        for character in str(value or "").upper()
        if character.isalnum()
    )


def _is_mlb_pull_or_training_writer(*values: Any) -> bool:
    text = _authority_text(*values)
    if any(token in text for token in LEGACY_TOKENS):
        return True
    return "MLB" in text and any(token in text for token in MLB_WRITER_TOKENS)


def _base_lambda_arn(value: Any) -> str:
    arn = str(value or "")
    parts = arn.split(":")
    if len(parts) >= 7 and parts[2] == "lambda" and parts[5] == "function":
        return ":".join(parts[:7])
    return arn


def _download_lambda_artifact(location: str) -> bytes:
    if not str(location or "").startswith("https://"):
        raise ValueError("Lambda code location is not an HTTPS URL")
    artifact = b""
    for attempt in range(1, 4):
        request = Request(
            location,
            headers={"User-Agent": "inqsi-mlb-deploy-identity-verifier/1.0"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=120) as response:
                content_length = response.headers.get("Content-Length")
                if (
                    content_length
                    and int(content_length) > MAX_COMPRESSED_ARTIFACT_BYTES
                ):
                    raise ValueError(
                        "Lambda deployment artifact exceeds the download limit"
                    )
                artifact = response.read(MAX_COMPRESSED_ARTIFACT_BYTES + 1)
            break
        except HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise
            if attempt == 3:
                raise
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(2 ** (attempt - 1))
    if len(artifact) > MAX_COMPRESSED_ARTIFACT_BYTES:
        raise ValueError("Lambda deployment artifact exceeds the download limit")
    return artifact


def _stack_outputs(cloudformation: Any, stack_name: str) -> Dict[str, str]:
    response = cloudformation.describe_stacks(StackName=stack_name)
    stacks = response.get("Stacks") or []
    if len(stacks) != 1:
        raise RuntimeError(f"expected one stack, found {len(stacks)}")
    return {
        str(output.get("OutputKey")): str(output.get("OutputValue") or "")
        for output in (stacks[0].get("Outputs") or [])
        if output.get("OutputKey")
    }


def verify(
    *,
    stack_name: str,
    region: str,
    expected_git_sha: str,
    expected_template_sha256: str,
    expected_deploy_run_id: str,
    expected_code_manifest: Dict[str, Any],
) -> Dict[str, Any]:
    deploy_run_id = str(expected_deploy_run_id or "").strip()
    if not deploy_run_id:
        raise ValueError("expected deploy run ID is missing")
    cloudformation = boto3.client("cloudformation", region_name=region)
    lambdas = boto3.client("lambda", region_name=region)
    events = boto3.client("events", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    blockers: List[str] = []
    manifest_functions = expected_code_manifest.get("functions") or {}
    code_manifest_identity_matches = bool(
        expected_code_manifest.get("schemaVersion") == MANIFEST_SCHEMA_VERSION
        and expected_code_manifest.get("expectedGitSha") == expected_git_sha
        and expected_code_manifest.get("expectedTemplateSha256")
        == expected_template_sha256
        and isinstance(manifest_functions, dict)
        and set(manifest_functions) == set(FUNCTIONS)
    )
    if not code_manifest_identity_matches:
        blockers.append("EXPECTED_LAMBDA_CODE_MANIFEST_IDENTITY_MISMATCH")
    function_proofs: Dict[str, Any] = {}
    function_arns: Dict[str, str] = {}
    lock_configuration: Dict[str, Any] = {
        "expectedHandler": LOCK_HANDLER,
        "expectedTimeoutSeconds": LOCK_TIMEOUT_SECONDS,
        "expectedAsyncRetryPolicy": dict(
            FUNCTION_ASYNC_RETRY_POLICIES["lock"]
        ),
        "expectedExecutionLeaseSeconds": int(
            LOCK_EXECUTION_LEASE_SECONDS
        ),
        "requiredEnvironmentKeys": list(LOCK_REQUIRED_ENVIRONMENT),
        "executionConcurrencyStrategy": (
            LOCK_EXECUTION_CONCURRENCY_STRATEGY
        ),
        "executionLeaseScope": "global_mlb_lock_execution",
        "executionLeaseSafetyMarginSeconds": (
            LOCK_EXECUTION_LEASE_SAFETY_MARGIN_SECONDS
        ),
        "expiredLeaseReclaim": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
        "matches": False,
    }
    trainer_configuration: Dict[str, Any] = {
        "expectedHandler": TRAINER_HANDLER,
        "expectedEnvironment": dict(TRAINER_EXPECTED_ENVIRONMENT),
        "requiredEnvironmentKeys": list(TRAINER_REQUIRED_ENVIRONMENT),
        "expectedAsyncRetryPolicy": dict(TRAINER_RETRY_POLICY),
        "expectedTimeoutSeconds": TRAINER_TIMEOUT_SECONDS,
        "executionConcurrencyStrategy": TRAINER_EXECUTION_CONCURRENCY_STRATEGY,
        "reservedLambdaConcurrencyRequired": False,
        "sqsFailureDestinationRequired": False,
        "matches": False,
    }
    lock_execution_configuration: Dict[str, Any] = {
        "strategy": "dynamodb_global_all_mutating_conditional_lease",
        "expectedTimeoutSeconds": LOCK_TIMEOUT_SECONDS,
        "expectedLeaseSeconds": int(LOCK_EXECUTION_LEASE_SECONDS),
        "scope": "scheduled_manual_and_forced_lock_runs",
        "statusReadsAcquireLease": False,
        "matches": False,
    }
    provider_credential_proof: Dict[str, Any] = {
        "provider": "Big Balls Sports Data",
        "exactGithubSecretName": "BBS_API_KEY",
        "runtimeSecretArnEnvironment": BBS_SECRET_ARN_ENVIRONMENT,
        "consumerRole": "ingest",
        "secretArnPresentOnIngest": False,
        "shadowEnvironmentMatches": False,
        "plaintextKeyEnvironmentAbsent": True,
        "retiredProviderEnvironmentAbsent": True,
        "otherCanonicalFunctionsWithoutBbsAuthority": True,
    }

    try:
        stack_outputs = _stack_outputs(cloudformation, stack_name)
    except Exception as exc:
        stack_outputs = {}
        blockers.append(f"STACK_OUTPUT_RESOLUTION_FAILED:{exc}")

    artifact_bucket_name = str(
        stack_outputs.get(TRAINER_ARTIFACT_BUCKET_OUTPUT) or ""
    )
    expected_trainer_arn = str(
        stack_outputs.get(TRAINER_FUNCTION_ARN_OUTPUT) or ""
    )
    if not artifact_bucket_name:
        blockers.append(
            f"STACK_OUTPUT_MISSING:{TRAINER_ARTIFACT_BUCKET_OUTPUT}"
        )
    if not expected_trainer_arn:
        blockers.append(f"STACK_OUTPUT_MISSING:{TRAINER_FUNCTION_ARN_OUTPUT}")
    if TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT in stack_outputs:
        blockers.append(
            f"TRAINER_SQS_FALLBACK_STACK_OUTPUT_PRESENT:"
            f"{TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT}"
        )

    for logical_id, role in FUNCTIONS.items():
        try:
            resource = cloudformation.describe_stack_resource(
                StackName=stack_name,
                LogicalResourceId=logical_id,
            )["StackResourceDetail"]
            physical_id = str(resource.get("PhysicalResourceId") or "")
            config = lambdas.get_function_configuration(FunctionName=physical_id)
        except Exception as exc:
            blockers.append(f"FUNCTION_RESOLUTION_FAILED:{logical_id}:{exc}")
            continue

        environment = (config.get("Environment") or {}).get("Variables") or {}
        actual_git_sha = str(environment.get("INQSI_DEPLOY_GIT_SHA") or "")
        actual_template_sha = str(environment.get("INQSI_DEPLOY_TEMPLATE_SHA256") or "")
        actual_deploy_run_id = str(environment.get("INQSI_DEPLOY_RUN_ID") or "")
        arn = str(config.get("FunctionArn") or "")
        function_arns[role] = arn
        if actual_git_sha != expected_git_sha:
            blockers.append(f"DEPLOY_GIT_SHA_MISMATCH:{logical_id}")
        if actual_template_sha != expected_template_sha256:
            blockers.append(f"DEPLOY_TEMPLATE_SHA_MISMATCH:{logical_id}")

        configuration_matches = True
        actual_state = str(config.get("State") or "")
        actual_update_status = str(config.get("LastUpdateStatus") or "")
        actual_code_sha = str(config.get("CodeSha256") or "")
        actual_version = str(config.get("Version") or "")
        for matches, blocker in (
            (
                actual_state == "Active",
                f"LAMBDA_STATE_NOT_ACTIVE:{logical_id}:{actual_state or 'MISSING'}",
            ),
            (
                actual_update_status == "Successful",
                f"LAMBDA_LAST_UPDATE_NOT_SUCCESSFUL:{logical_id}:"
                f"{actual_update_status or 'MISSING'}",
            ),
            (bool(actual_code_sha), f"LAMBDA_CODE_SHA256_MISSING:{logical_id}"),
            (
                actual_version == "$LATEST",
                f"LAMBDA_VERSION_NOT_LATEST:{logical_id}:"
                f"{actual_version or 'MISSING'}",
            ),
            (
                actual_deploy_run_id == deploy_run_id,
                f"DEPLOY_RUN_ID_MISMATCH:{logical_id}",
            ),
        ):
            if not matches:
                configuration_matches = False
                blockers.append(blocker)

        expected_artifact_manifest = manifest_functions.get(logical_id)
        actual_artifact_manifest = None
        downloaded_code_sha = None
        code_sha_matches = False
        code_artifact_matches = False
        try:
            function = lambdas.get_function(FunctionName=physical_id)
            function_configuration = function.get("Configuration") or {}
            function_code_sha = str(
                function_configuration.get("CodeSha256") or ""
            )
            if function_code_sha != actual_code_sha:
                raise ValueError(
                    "GetFunction and GetFunctionConfiguration CodeSha256 differ"
                )
            location = str((function.get("Code") or {}).get("Location") or "")
            artifact = _download_lambda_artifact(location)
            downloaded_code_sha = lambda_code_sha256(artifact)
            code_sha_matches = downloaded_code_sha == actual_code_sha
            if not code_sha_matches:
                raise ValueError("downloaded artifact CodeSha256 differs from Lambda")
            actual_artifact_manifest = zip_content_manifest(artifact)
            code_artifact_matches = bool(
                code_manifest_identity_matches
                and isinstance(expected_artifact_manifest, dict)
                and actual_artifact_manifest == expected_artifact_manifest
            )
            if not code_artifact_matches:
                raise ValueError("deployed artifact content differs from clean SAM build")
        except Exception as exc:
            configuration_matches = False
            blockers.append(
                f"LAMBDA_CODE_ARTIFACT_VERIFICATION_FAILED:{logical_id}:{exc}"
            )
        expected_async_retry_policy = FUNCTION_ASYNC_RETRY_POLICIES.get(role)
        async_retry_policy: Dict[str, Any] = {}
        async_destination_config: Any = None
        async_destination_config_present = False
        configured_async_destinations: Dict[str, Dict[str, str]] = {}
        async_destination_config_valid = False
        if expected_async_retry_policy is not None:
            blocker_prefix = (
                "TRAINER_LAMBDA"
                if role == "trainer"
                else f"LAMBDA_ASYNC:{role.upper()}"
            )
            try:
                async_config = lambdas.get_function_event_invoke_config(
                    FunctionName=physical_id,
                    Qualifier="$LATEST",
                )
                async_retry_policy = {
                    "MaximumEventAgeInSeconds": async_config.get(
                        "MaximumEventAgeInSeconds"
                    ),
                    "MaximumRetryAttempts": async_config.get(
                        "MaximumRetryAttempts"
                    ),
                }
                raw_async_destination_config = async_config.get(
                    "DestinationConfig",
                    _MISSING_ASYNC_DESTINATION_CONFIG,
                )
                async_destination_config_present = (
                    raw_async_destination_config
                    is not _MISSING_ASYNC_DESTINATION_CONFIG
                )
                async_destination_config = (
                    raw_async_destination_config
                    if async_destination_config_present
                    else None
                )
                try:
                    configured_async_destinations = (
                        _normalize_async_destination_config(
                            raw_async_destination_config
                        )
                    )
                    async_destination_config_valid = True
                except ValueError as exc:
                    configuration_matches = False
                    blockers.append(
                        f"{blocker_prefix}_ASYNC_DESTINATION_CONFIG_INVALID:"
                        f"{exc}"
                    )
                if async_retry_policy != expected_async_retry_policy:
                    configuration_matches = False
                    blockers.append(
                        f"{blocker_prefix}_ASYNC_RETRY_POLICY_MISMATCH:"
                        f"expected={expected_async_retry_policy}:"
                        f"actual={async_retry_policy}"
                    )
                if configured_async_destinations:
                    configuration_matches = False
                    blockers.append(
                        f"{blocker_prefix}_ASYNC_DESTINATION_CONFIG_PRESENT"
                    )
            except Exception as exc:
                configuration_matches = False
                blockers.append(
                    f"{blocker_prefix}_ASYNC_RETRY_POLICY_CHECK_FAILED:{exc}"
                )
        retired_present = sorted(
            key for key in RETIRED_PROVIDER_ENVIRONMENT if key in environment
        )
        if retired_present:
            configuration_matches = False
            provider_credential_proof["retiredProviderEnvironmentAbsent"] = False
            blockers.append(
                f"RETIRED_PROVIDER_ENVIRONMENT_PRESENT:{logical_id}:"
                + ",".join(retired_present)
            )
        if BBS_FORBIDDEN_PLAINTEXT_ENVIRONMENT in environment:
            configuration_matches = False
            provider_credential_proof["plaintextKeyEnvironmentAbsent"] = False
            blockers.append(f"BBS_PLAINTEXT_KEY_ENVIRONMENT_PRESENT:{logical_id}")
        if role == "ingest":
            secret_arn_present = bool(
                str(environment.get(BBS_SECRET_ARN_ENVIRONMENT) or "").strip()
            )
            provider_credential_proof["secretArnPresentOnIngest"] = secret_arn_present
            if not secret_arn_present:
                configuration_matches = False
                blockers.append("BBS_SECRET_ARN_MISSING_ON_INGEST")
            shadow_environment_matches = all(
                str(environment.get(key) or "") == expected
                for key, expected in BBS_EXPECTED_INGEST_ENVIRONMENT.items()
            ) and bool(str(environment.get("BBS_SHADOW_S3_BUCKET") or "").strip())
            provider_credential_proof["shadowEnvironmentMatches"] = shadow_environment_matches
            if not shadow_environment_matches:
                configuration_matches = False
                blockers.append("BBS_SHADOW_ENVIRONMENT_MISMATCH_ON_INGEST")
        else:
            leaked = sorted(
                key
                for key in environment
                if str(key).startswith("BBS_")
                or str(key).startswith("BbsApi")
            )
            if leaked:
                configuration_matches = False
                provider_credential_proof["otherCanonicalFunctionsWithoutBbsAuthority"] = False
                blockers.append(
                    f"BBS_AUTHORITY_LEAKED_TO_{role.upper()}:" + ",".join(leaked)
                )
        if role == "lock":
            actual_lock_handler = str(config.get("Handler") or "")
            actual_lock_timeout = config.get("Timeout")
            actual_lock_lease_seconds_text = str(
                environment.get("MLB_LOCK_EXECUTION_LEASE_SECONDS") or ""
            )
            try:
                actual_lock_lease_seconds = int(
                    actual_lock_lease_seconds_text
                )
            except (TypeError, ValueError):
                actual_lock_lease_seconds = None
            lock_lease_duration_matches = (
                actual_lock_lease_seconds_text
                == LOCK_EXECUTION_LEASE_SECONDS
            )
            lock_lease_covers_timeout_safety_bound = bool(
                isinstance(actual_lock_timeout, int)
                and actual_lock_lease_seconds is not None
                and actual_lock_lease_seconds
                >= actual_lock_timeout
                + LOCK_EXECUTION_LEASE_SAFETY_MARGIN_SECONDS
            )
            missing_lock_environment = [
                key
                for key in LOCK_REQUIRED_ENVIRONMENT
                if not str(environment.get(key) or "").strip()
            ]
            if actual_lock_handler != LOCK_HANDLER:
                configuration_matches = False
                blockers.append(
                    "LOCK_HANDLER_MISMATCH:"
                    f"expected={LOCK_HANDLER}:actual={actual_lock_handler}"
                )
            if actual_lock_timeout != LOCK_TIMEOUT_SECONDS:
                configuration_matches = False
                blockers.append(
                    "LOCK_TIMEOUT_MISMATCH:"
                    f"expected={LOCK_TIMEOUT_SECONDS}:actual={actual_lock_timeout}"
                )
            if not lock_lease_duration_matches:
                configuration_matches = False
                blockers.append(
                    "LOCK_EXECUTION_LEASE_DURATION_MISMATCH:"
                    f"expected={LOCK_EXECUTION_LEASE_SECONDS}:"
                    f"actual={actual_lock_lease_seconds_text or 'MISSING'}"
                )
            if not lock_lease_covers_timeout_safety_bound:
                configuration_matches = False
                blockers.append(
                    "LOCK_EXECUTION_LEASE_TIMEOUT_BOUND_FAILED:"
                    f"timeout={actual_lock_timeout}:"
                    f"margin={LOCK_EXECUTION_LEASE_SAFETY_MARGIN_SECONDS}:"
                    f"lease={actual_lock_lease_seconds_text or 'MISSING'}"
                )
            for key in missing_lock_environment:
                configuration_matches = False
                blockers.append(f"LOCK_ENVIRONMENT_MISSING:{key}")
            lock_configuration.update(
                {
                    "handler": actual_lock_handler,
                    "timeoutSeconds": actual_lock_timeout,
                    "timeoutMatches": (
                        actual_lock_timeout == LOCK_TIMEOUT_SECONDS
                    ),
                    "executionConcurrencyStrategy": (
                        LOCK_EXECUTION_CONCURRENCY_STRATEGY
                    ),
                    "executionLeaseScope": "global_mlb_lock_execution",
                    "executionLeaseSeconds": actual_lock_lease_seconds,
                    "executionLeaseDurationMatches": (
                        lock_lease_duration_matches
                    ),
                    "executionLeaseSafetyMarginSeconds": (
                        LOCK_EXECUTION_LEASE_SAFETY_MARGIN_SECONDS
                    ),
                    "executionLeaseCoversTimeoutSafetyBound": (
                        lock_lease_covers_timeout_safety_bound
                    ),
                    "requiredEnvironmentKeys": list(
                        LOCK_REQUIRED_ENVIRONMENT
                    ),
                    "snapshotTable": str(
                        environment.get("SNAPSHOTS_TABLE") or ""
                    ),
                    "requiredEnvironmentPresent": (
                        not missing_lock_environment
                    ),
                    "expiredLeaseReclaim": True,
                    "ownerConditionalRelease": True,
                    "reservedLambdaConcurrencyRequired": False,
                    "asyncRetryPolicy": async_retry_policy,
                    "asyncRetryPolicyMatches": (
                        async_retry_policy
                        == FUNCTION_ASYNC_RETRY_POLICIES["lock"]
                    ),
                    "asyncDestinationConfig": async_destination_config,
                    "asyncDestinationConfigPresent": (
                        async_destination_config_present
                    ),
                    "asyncDestinationConfigValid": (
                        async_destination_config_valid
                    ),
                    "configuredAsyncDestinations": (
                        configured_async_destinations
                    ),
                    "asyncDestinationConfigAbsent": bool(
                        async_destination_config_valid
                        and not configured_async_destinations
                    ),
                    "matches": configuration_matches,
                }
            )
        if role == "trainer":
            actual_handler = str(config.get("Handler") or "")
            function_dead_letter_config = dict(
                config.get("DeadLetterConfig") or {}
            )
            if function_dead_letter_config:
                configuration_matches = False
                blockers.append("TRAINER_FUNCTION_DEAD_LETTER_CONFIG_PRESENT")
            if actual_handler != TRAINER_HANDLER:
                configuration_matches = False
                blockers.append(
                    "TRAINER_HANDLER_MISMATCH:"
                    f"expected={TRAINER_HANDLER}:actual={actual_handler}"
                )
            actual_timeout = config.get("Timeout")
            if actual_timeout != TRAINER_TIMEOUT_SECONDS:
                configuration_matches = False
                blockers.append(
                    "TRAINER_TIMEOUT_MISMATCH:"
                    f"expected={TRAINER_TIMEOUT_SECONDS}:actual={actual_timeout}"
                )
            for key, expected_value in TRAINER_EXPECTED_ENVIRONMENT.items():
                actual_value = str(environment.get(key) or "")
                if actual_value != expected_value:
                    configuration_matches = False
                    blockers.append(
                        f"TRAINER_ENVIRONMENT_MISMATCH:{key}:"
                        f"expected={expected_value}:actual={actual_value}"
                    )
            for key in TRAINER_REQUIRED_ENVIRONMENT:
                if not str(environment.get(key) or "").strip():
                    configuration_matches = False
                    blockers.append(f"TRAINER_ENVIRONMENT_MISSING:{key}")
            actual_bucket = str(environment.get("MLB_ML_ARTIFACTS_BUCKET") or "")
            if artifact_bucket_name and actual_bucket != artifact_bucket_name:
                configuration_matches = False
                blockers.append(
                    "TRAINER_ARTIFACT_BUCKET_MISMATCH:"
                    f"expected={artifact_bucket_name}:actual={actual_bucket}"
                )
            if expected_trainer_arn and arn != expected_trainer_arn:
                configuration_matches = False
                blockers.append(
                    "TRAINER_FUNCTION_ARN_OUTPUT_MISMATCH:"
                    f"expected={expected_trainer_arn}:actual={arn}"
                )
            reserved_concurrency = None
            try:
                concurrency = lambdas.get_function_concurrency(
                    FunctionName=physical_id
                )
                reserved_concurrency = concurrency.get(
                    "ReservedConcurrentExecutions"
                )
                if reserved_concurrency is not None:
                    configuration_matches = False
                    blockers.append(
                        "TRAINER_RESERVED_CONCURRENCY_PRESENT:"
                        f"actual={reserved_concurrency}"
                    )
            except Exception as exc:
                configuration_matches = False
                blockers.append(
                    "TRAINER_RESERVED_CONCURRENCY_ABSENCE_CHECK_FAILED:"
                    f"{exc}"
                )
            safe_environment_keys = (
                *TRAINER_REQUIRED_ENVIRONMENT,
                *TRAINER_EXPECTED_ENVIRONMENT.keys(),
                "INQSI_DEPLOY_GIT_SHA",
                "INQSI_DEPLOY_TEMPLATE_SHA256",
                "INQSI_DEPLOY_RUN_ID",
            )
            trainer_configuration.update({
                "handler": actual_handler,
                "functionArn": arn,
                "stackOutputFunctionArn": expected_trainer_arn or None,
                "timeoutSeconds": actual_timeout,
                "timeoutMatches": actual_timeout == TRAINER_TIMEOUT_SECONDS,
                "reservedConcurrentExecutions": reserved_concurrency,
                "reservedConcurrencyAbsent": reserved_concurrency is None,
                "executionConcurrencyStrategy": (
                    TRAINER_EXECUTION_CONCURRENCY_STRATEGY
                ),
                "executionLeaseSeconds": int(TRAINER_EXECUTION_LEASE_SECONDS),
                "reservedLambdaConcurrencyRequired": False,
                "functionDeadLetterConfig": function_dead_letter_config,
                "functionDeadLetterConfigAbsent": not bool(
                    function_dead_letter_config
                ),
                "asyncRetryPolicy": async_retry_policy,
                "asyncRetryPolicyMatches": (
                    async_retry_policy == TRAINER_RETRY_POLICY
                ),
                "asyncDestinationConfig": async_destination_config,
                "asyncDestinationConfigPresent": (
                    async_destination_config_present
                ),
                "asyncDestinationConfigValid": (
                    async_destination_config_valid
                ),
                "configuredAsyncDestinations": (
                    configured_async_destinations
                ),
                "asyncDestinationConfigAbsent": bool(
                    async_destination_config_valid
                    and not configured_async_destinations
                ),
                "environment": {
                    key: environment.get(key)
                    for key in safe_environment_keys
                },
                "matches": configuration_matches,
            })
        if role == "lock":
            actual_timeout = config.get("Timeout")
            if actual_timeout != LOCK_TIMEOUT_SECONDS:
                configuration_matches = False
                blockers.append(
                    "LOCK_TIMEOUT_MISMATCH:"
                    f"expected={LOCK_TIMEOUT_SECONDS}:actual={actual_timeout}"
                )
            for key, expected_value in LOCK_EXPECTED_ENVIRONMENT.items():
                actual_value = str(environment.get(key) or "")
                if actual_value != expected_value:
                    configuration_matches = False
                    blockers.append(
                        f"LOCK_ENVIRONMENT_MISMATCH:{key}:"
                        f"expected={expected_value}:actual={actual_value}"
                    )
            lock_execution_configuration.update(
                {
                    "timeoutSeconds": actual_timeout,
                    "leaseSeconds": int(
                        str(
                            environment.get("MLB_LOCK_EXECUTION_LEASE_SECONDS")
                            or "0"
                        )
                    ),
                    "leaseOutlivesTimeout": bool(
                        actual_timeout == LOCK_TIMEOUT_SECONDS
                        and int(
                            str(
                                environment.get(
                                    "MLB_LOCK_EXECUTION_LEASE_SECONDS"
                                )
                                or "0"
                            )
                        )
                        > LOCK_TIMEOUT_SECONDS
                    ),
                    "matches": configuration_matches,
                }
            )

        function_proofs[logical_id] = {
            "role": role,
            "physicalId": physical_id,
            "functionArn": arn,
            "handler": config.get("Handler"),
            "runtime": config.get("Runtime"),
            "state": actual_state or None,
            "lastUpdateStatus": actual_update_status or None,
            "lastModified": config.get("LastModified"),
            "codeSha256": actual_code_sha or None,
            "codeSha256Present": bool(actual_code_sha),
            "downloadedCodeSha256": downloaded_code_sha,
            "downloadedCodeSha256Matches": code_sha_matches,
            "expectedCodeContentManifest": expected_artifact_manifest,
            "deployedCodeContentManifest": actual_artifact_manifest,
            "codeArtifactMatchesCleanBuild": code_artifact_matches,
            "version": actual_version or None,
            "deployGitSha": actual_git_sha or None,
            "deployTemplateSha256": actual_template_sha or None,
            "deployRunId": actual_deploy_run_id or None,
            "deployRunIdMatches": actual_deploy_run_id == deploy_run_id,
            "identityMatches": (
                actual_git_sha == expected_git_sha
                and actual_template_sha == expected_template_sha256
                and actual_deploy_run_id == deploy_run_id
                and code_artifact_matches
            ),
            "configurationMatches": configuration_matches,
            "asyncDeliveryPolicy": {
                "expectedRetryPolicy": expected_async_retry_policy,
                "actualRetryPolicy": async_retry_policy,
                "retryPolicyMatches": (
                    async_retry_policy == expected_async_retry_policy
                    if expected_async_retry_policy is not None
                    else None
                ),
                "destinationConfigPresent": async_destination_config_present,
                "destinationConfigValid": async_destination_config_valid,
                "configuredDestinations": configured_async_destinations,
                "destinationConfigAbsent": (
                    bool(
                        async_destination_config_valid
                        and not configured_async_destinations
                    )
                    if expected_async_retry_policy is not None
                    else None
                ),
            },
        }

    artifact_bucket_proof: Dict[str, Any] = {
        "stackOutputKey": TRAINER_ARTIFACT_BUCKET_OUTPUT,
        "bucketName": artifact_bucket_name or None,
        "versioningStatus": None,
        "versioningEnabled": False,
        "serverSideEncryption": None,
        "encrypted": False,
        "publicAccessBlocked": False,
    }
    if artifact_bucket_name:
        try:
            versioning = s3.get_bucket_versioning(Bucket=artifact_bucket_name)
            versioning_status = str(versioning.get("Status") or "")
            artifact_bucket_proof["versioningStatus"] = versioning_status or None
            artifact_bucket_proof["versioningEnabled"] = versioning_status == "Enabled"
            if versioning_status != "Enabled":
                blockers.append(
                    "TRAINER_ARTIFACT_BUCKET_VERSIONING_NOT_ENABLED:"
                    f"bucket={artifact_bucket_name}:status={versioning_status or 'MISSING'}"
                )
        except Exception as exc:
            blockers.append(
                "TRAINER_ARTIFACT_BUCKET_VERSIONING_CHECK_FAILED:"
                f"bucket={artifact_bucket_name}:{exc}"
            )
        try:
            encryption = s3.get_bucket_encryption(Bucket=artifact_bucket_name)
            rules = (
                (encryption.get("ServerSideEncryptionConfiguration") or {}).get(
                    "Rules"
                )
                or []
            )
            algorithms = sorted(
                {
                    str(
                        (rule.get("ApplyServerSideEncryptionByDefault") or {}).get(
                            "SSEAlgorithm"
                        )
                        or ""
                    )
                    for rule in rules
                    if isinstance(rule, dict)
                }
                - {""}
            )
            artifact_bucket_proof["serverSideEncryption"] = algorithms
            artifact_bucket_proof["encrypted"] = bool(algorithms)
            if not algorithms:
                blockers.append(
                    "TRAINER_ARTIFACT_BUCKET_ENCRYPTION_NOT_ENABLED:"
                    f"bucket={artifact_bucket_name}"
                )
        except Exception as exc:
            blockers.append(
                "TRAINER_ARTIFACT_BUCKET_ENCRYPTION_CHECK_FAILED:"
                f"bucket={artifact_bucket_name}:{exc}"
            )
        try:
            public_access = (
                s3.get_public_access_block(Bucket=artifact_bucket_name).get(
                    "PublicAccessBlockConfiguration"
                )
                or {}
            )
            required_public_block = (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
            public_blocked = all(
                public_access.get(key) is True for key in required_public_block
            )
            artifact_bucket_proof["publicAccessBlocked"] = public_blocked
            if not public_blocked:
                blockers.append(
                    "TRAINER_ARTIFACT_BUCKET_PUBLIC_ACCESS_NOT_BLOCKED:"
                    f"bucket={artifact_bucket_name}"
                )
        except Exception as exc:
            blockers.append(
                "TRAINER_ARTIFACT_BUCKET_PUBLIC_ACCESS_CHECK_FAILED:"
                f"bucket={artifact_bucket_name}:{exc}"
            )

    regional_rule_inventory: List[Dict[str, Any]] = []
    regional_rule_inventory_complete = False
    try:
        for rule_name in _all_rule_names(events):
            rule = events.describe_rule(Name=rule_name)
            regional_rule_inventory.append({
                "name": rule_name,
                "state": rule.get("State"),
                "schedule": rule.get("ScheduleExpression"),
                "targets": _targets_for_rule(events, rule_name),
            })
        regional_rule_inventory_complete = True
    except Exception as exc:
        blockers.append(f"EVENTBRIDGE_REGIONAL_RULE_DISCOVERY_FAILED:{exc}")

    schedule_proofs: Dict[str, Any] = {}
    for role, expected in EXPECTED_SCHEDULES.items():
        arn = function_arns.get(role)
        if not arn:
            blockers.append(f"SCHEDULE_TARGET_FUNCTION_MISSING:{role}")
            continue
        base_arn = _base_lambda_arn(arn)
        enabled_rules: List[Dict[str, Any]] = []
        for rule in regional_rule_inventory:
            targets = rule.get("targets") or []
            matching_targets = [
                target
                for target in targets
                if _base_lambda_arn(target.get("Arn")) == base_arn
            ]
            if rule.get("state") in ENABLED_RULE_STATES and matching_targets:
                enabled_rules.append({
                    "name": rule.get("name"),
                    "state": rule.get("state"),
                    "schedule": rule.get("schedule"),
                    "targetArns": sorted(str(target.get("Arn") or "") for target in targets),
                    "matchingTargets": matching_targets,
                })
        schedules = sorted(str(rule.get("schedule") or "") for rule in enabled_rules)
        if schedules != sorted(expected):
            blockers.append(f"SCHEDULE_MISMATCH:{role}:expected={expected}:actual={schedules}")
        target_topology_matches = bool(
            len(enabled_rules) == len(expected)
            and all(
                len(rule.get("matchingTargets") or []) == 1
                and len(rule.get("targetArns") or []) == 1
                and str(
                    (rule.get("matchingTargets") or [{}])[0].get("Arn")
                    or ""
                )
                == arn
                for rule in enabled_rules
            )
        )
        if not target_topology_matches:
            blockers.append(f"EVENTBRIDGE_TARGET_TOPOLOGY_MISMATCH:{role}")
        expected_retry_policies = SCHEDULE_RETRY_POLICIES.get(role)
        retry_policy_matches = None
        dead_letter_absent = None
        invocation_inputs_match = None
        if expected_retry_policies is not None:
            retry_policy_matches = bool(
                enabled_rules
                and all(
                    all(
                        (target.get("RetryPolicy") or {})
                        == expected_retry_policies.get(
                            str(rule.get("schedule") or ""), {}
                        )
                        for target in rule.get("matchingTargets") or []
                    )
                    for rule in enabled_rules
                )
            )
            dead_letter_absent = bool(
                enabled_rules
                and all(
                    all(
                        not bool(target.get("DeadLetterConfig"))
                        for target in rule.get("matchingTargets") or []
                    )
                    for rule in enabled_rules
                )
            )
            if not retry_policy_matches:
                blockers.append(
                    "TRAINER_EVENTBRIDGE_RETRY_POLICY_MISMATCH"
                    if role == "trainer"
                    else f"EVENTBRIDGE_RETRY_POLICY_MISMATCH:{role}"
                )
            if not dead_letter_absent:
                blockers.append(
                    "TRAINER_EVENTBRIDGE_FAILURE_DESTINATION_PRESENT"
                    if role == "trainer"
                    else f"EVENTBRIDGE_FAILURE_DESTINATION_PRESENT:{role}"
                )
        expected_role_invocations = SCHEDULE_EXPECTED_INVOCATIONS.get(role)
        if expected_role_invocations is not None:
            expected_invocations = sorted(
                (
                    str(invocation["schedule"]),
                    json.dumps(invocation["input"], sort_keys=True, separators=(",", ":")),
                )
                for invocation in expected_role_invocations
            )
            actual_invocations: List[tuple[str, str]] = []
            malformed_input = False
            for rule in enabled_rules:
                for target in rule.get("matchingTargets") or []:
                    try:
                        parsed_input = json.loads(str(target.get("Input") or ""))
                    except (TypeError, ValueError):
                        malformed_input = True
                        continue
                    if not isinstance(parsed_input, dict):
                        malformed_input = True
                        continue
                    actual_invocations.append(
                        (
                            str(rule.get("schedule") or ""),
                            json.dumps(parsed_input, sort_keys=True, separators=(",", ":")),
                        )
                    )
            invocation_inputs_match = (
                not malformed_input
                and sorted(actual_invocations) == expected_invocations
            )
            if not invocation_inputs_match:
                blockers.append(
                    "TRAINER_EVENTBRIDGE_INVOCATION_INPUT_MISMATCH"
                    if role == "trainer"
                    else f"EVENTBRIDGE_INVOCATION_INPUT_MISMATCH:{role}"
                )
        schedule_proofs[role] = {
            "functionArn": arn,
            "enabledRules": enabled_rules,
            "expectedSchedules": expected,
            "exactMatch": schedules == sorted(expected),
            "targetTopologyMatches": target_topology_matches,
            "retryPolicyMatches": retry_policy_matches,
            "deadLetterQueueAbsent": dead_letter_absent,
            "deliveryPolicyMatches": bool(
                retry_policy_matches and dead_letter_absent
            ) if expected_retry_policies is not None else None,
            "invocationInputsMatch": invocation_inputs_match,
            "sqsFailureDestinationRequired": False if expected_retry_policies is not None else None,
            "expectedRetryPolicy": expected_retry_policies,
        }

    alternate_writer_rules: List[Dict[str, Any]] = []
    discovered_writer_functions: List[Dict[str, Any]] = []
    canonical_writer_arns = {
        _base_lambda_arn(arn)
        for role, arn in function_arns.items()
        if role in {"ingest", "trainer"}
    }
    try:
        writer_functions_by_arn: Dict[str, Dict[str, Any]] = {}
        for function in _all_lambda_functions(lambdas):
            name = str(function.get("FunctionName") or "")
            arn = str(function.get("FunctionArn") or "")
            handler = str(function.get("Handler") or "")
            description = str(function.get("Description") or "")
            environment = (function.get("Environment") or {}).get("Variables") or {}
            retired_present = sorted(
                key for key in RETIRED_PROVIDER_ENVIRONMENT if key in environment
            )
            if retired_present:
                provider_credential_proof["retiredProviderEnvironmentAbsent"] = False
                blockers.append(
                    f"RETIRED_PROVIDER_ENVIRONMENT_PRESENT_ON_DISCOVERED_LAMBDA:{name}:"
                    + ",".join(retired_present)
                )
            if BBS_FORBIDDEN_PLAINTEXT_ENVIRONMENT in environment:
                provider_credential_proof["plaintextKeyEnvironmentAbsent"] = False
                blockers.append(
                    f"BBS_PLAINTEXT_KEY_ENVIRONMENT_PRESENT_ON_DISCOVERED_LAMBDA:{name}"
                )
            bbs_authority_keys = sorted(
                key for key in environment if str(key).startswith("BBS_")
            )
            if bbs_authority_keys and _base_lambda_arn(arn) != _base_lambda_arn(function_arns.get("ingest")):
                provider_credential_proof["otherCanonicalFunctionsWithoutBbsAuthority"] = False
                blockers.append(
                    f"BBS_AUTHORITY_PRESENT_ON_NON_INGEST_LAMBDA:{name}:"
                    + ",".join(bbs_authority_keys)
                )
            if not arn or not _is_mlb_pull_or_training_writer(
                name, handler, description
            ):
                continue
            base_arn = _base_lambda_arn(arn)
            proof = {
                "functionName": name,
                "functionArn": arn,
                "handler": handler or None,
                "canonicalWriter": base_arn in canonical_writer_arns,
            }
            writer_functions_by_arn[base_arn] = proof
            discovered_writer_functions.append(proof)

        for rule in regional_rule_inventory:
            if rule.get("state") not in ENABLED_RULE_STATES:
                continue
            rule_name = str(rule.get("name") or "")
            targets = rule.get("targets") or []
            rule_looks_like_writer = _is_mlb_pull_or_training_writer(rule_name)
            relevant_targets = []
            for target in targets:
                target_arn = str(target.get("Arn") or "")
                target_base_arn = _base_lambda_arn(target_arn)
                function = writer_functions_by_arn.get(target_base_arn)
                target_looks_like_writer = bool(
                    function
                    or _is_mlb_pull_or_training_writer(target_arn)
                )
                if not rule_looks_like_writer and not target_looks_like_writer:
                    continue
                relevant_targets.append({
                    "arn": target_arn,
                    "id": target.get("Id"),
                    "knownWriterFunction": function,
                    "canonicalWriter": target_base_arn in canonical_writer_arns,
                })
            if not relevant_targets:
                continue
            alternate_targets = [
                target
                for target in relevant_targets
                if target.get("canonicalWriter") is not True
            ]
            legacy_named_rule = any(
                token in _authority_text(rule_name) for token in LEGACY_TOKENS
            )
            if alternate_targets or legacy_named_rule:
                alternate_writer_rules.append({
                    "name": rule_name,
                    "state": rule.get("state"),
                    "schedule": rule.get("schedule"),
                    "legacyNamedRule": legacy_named_rule,
                    "targets": relevant_targets,
                })
    except Exception as exc:
        blockers.append(f"MLB_ALTERNATE_WRITER_DISCOVERY_FAILED:{exc}")

    discovered_writer_functions = sorted(
        discovered_writer_functions,
        key=lambda item: (str(item.get("functionName") or ""), str(item.get("functionArn") or "")),
    )
    alternate_writer_rules = sorted(
        alternate_writer_rules, key=lambda item: str(item.get("name") or "")
    )
    if alternate_writer_rules:
        blockers.append("ENABLED_ALTERNATE_MLB_PULL_OR_TRAINING_WRITERS")
    if any(rule.get("legacyNamedRule") is True for rule in alternate_writer_rules):
        blockers.append("ENABLED_LEGACY_MLB_EVENTBRIDGE_RULES")

    return {
        "ok": not blockers,
        "proofType": "INQSI_MLB_DEPLOY_IDENTITY_AND_SCHEDULE_PROOF",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "stackName": stack_name,
        "region": region,
        "expectedGitSha": expected_git_sha,
        "expectedTemplateSha256": expected_template_sha256,
        "expectedDeployRunId": deploy_run_id,
        "expectedCodeManifest": {
            "schemaVersion": expected_code_manifest.get("schemaVersion"),
            "expectedGitSha": expected_code_manifest.get("expectedGitSha"),
            "expectedTemplateSha256": expected_code_manifest.get(
                "expectedTemplateSha256"
            ),
            "identityMatches": code_manifest_identity_matches,
        },
        "functions": function_proofs,
        "lockConfiguration": lock_configuration,
        "trainerConfiguration": trainer_configuration,
        "lockExecutionConfiguration": lock_execution_configuration,
        "providerCredentialBoundary": provider_credential_proof,
        "artifactBucket": artifact_bucket_proof,
        "schedules": schedule_proofs,
        "eventBridgeDefaultBusInventoryComplete": (
            regional_rule_inventory_complete
        ),
        "enabledLegacyRules": alternate_writer_rules,
        "alternateWriterAuthority": {
            "canonicalWriterArns": sorted(canonical_writer_arns),
            "discoveredWriterFunctions": discovered_writer_functions,
            "enabledAlternateRules": alternate_writer_rules,
            "eventBridgeScope": "default_bus_rules",
            "scanComplete": not any(
                blocker.startswith("MLB_ALTERNATE_WRITER_DISCOVERY_FAILED:")
                or blocker.startswith("EVENTBRIDGE_REGIONAL_RULE_DISCOVERY_FAILED:")
                for blocker in blockers
            ),
        },
        "blockers": blockers,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    parser.add_argument("--expected-deploy-run-id", required=True)
    parser.add_argument("--expected-code-manifest", required=True)
    parser.add_argument("--output", default="runtime_reports/mlb_deploy_identity_latest.json")
    args = parser.parse_args()

    expected_code_manifest = json.loads(
        Path(args.expected_code_manifest).read_text(encoding="utf-8")
    )
    if not isinstance(expected_code_manifest, dict):
        raise SystemExit("Expected Lambda code manifest must be a JSON object")
    result = verify(
        stack_name=args.stack_name,
        region=args.region,
        expected_git_sha=args.expected_git_sha,
        expected_template_sha256=args.expected_template_sha256,
        expected_deploy_run_id=args.expected_deploy_run_id,
        expected_code_manifest=expected_code_manifest,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True:
        raise SystemExit("MLB deployment identity verification failed: " + json.dumps(result.get("blockers")))


if __name__ == "__main__":
    main()
