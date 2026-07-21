from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import boto3


FUNCTIONS = {
    "MLBAuditedPullFunction": "ingest",
    "MLBDailyPickLockFunction": "lock",
    "MLBMLTrainingFunction": "trainer",
    "MLBProductionVerifierFunction": "verifier",
    "MLBV3ReadFunction": "read",
    "MLBResultsSchedulerFunction": "settlement",
}

EXPECTED_SCHEDULES = {
    "ingest": ["cron(0/15 * * * ? *)"],
    "lock": ["rate(1 minute)"],
    "trainer": ["rate(6 hours)", "rate(15 minutes)"],
    "verifier": ["rate(5 minutes)"],
    "settlement": ["rate(15 minutes)"],
}

TRAINER_EXPECTED_INVOCATIONS = (
    {
        "schedule": "rate(6 hours)",
        "input": {
            "sport": "mlb",
            "mode": "scheduled",
            "run": "aws_native_fixed_prospective_shadow_training",
        },
    },
    {
        "schedule": "rate(15 minutes)",
        "input": {
            "sport": "mlb",
            "mode": "selection_capture",
            "run": "aws_native_prospective_selection_capture",
        },
    },
)

TRAINER_HANDLER = "mlb_ml_aws_training_v1.lambda_handler"
TRAINER_TIMEOUT_SECONDS = 900
TRAINER_EXECUTION_CONCURRENCY_STRATEGY = "dynamodb_conditional_lease"
TRAINER_EXECUTION_LEASE_SECONDS = "960"
TRAINER_ARTIFACT_BUCKET_OUTPUT = "MLBMLArtifactsBucketName"
TRAINER_FUNCTION_ARN_OUTPUT = "MLBMLTrainingFunctionArn"
TRAINER_FORBIDDEN_DLQ_ARN_OUTPUT = "MLBMLTrainingDeadLetterQueueArn"
TRAINER_RETRY_POLICY = {
    "MaximumEventAgeInSeconds": 21600,
    "MaximumRetryAttempts": 2,
}
TRAINER_EXPECTED_ENVIRONMENT = {
    "MLB_ML_EXPERIMENT_ID": "mlb-v2-2026-07-21-future-prospective-r2",
    "MLB_ML_RELEASE_CONTRACT_ID": "mlb-v2-2026-07-21-future-prospective-r2",
    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-07-22T04:00:00+00:00",
    "MLB_ML_FEATURE_VECTOR_VERSION": (
        "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness"
    ),
    "MLB_ML_EXECUTION_LEASE_SECONDS": TRAINER_EXECUTION_LEASE_SECONDS,
    "INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION": "false",
    "INQSI_MLB_LEGACY_V1_AUTHORITY_ENABLED": "false",
    "INQSI_MLB_ML_AUTO_PROMOTE": "false",
}
TRAINER_REQUIRED_ENVIRONMENT = (
    "MLB_ML_ARTIFACTS_BUCKET",
    "SNAPSHOTS_TABLE",
    "OUTCOMES_TABLE",
)

_MISSING_ASYNC_DESTINATION_CONFIG = object()

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


def verify(*, stack_name: str, region: str, expected_git_sha: str, expected_template_sha256: str) -> Dict[str, Any]:
    cloudformation = boto3.client("cloudformation", region_name=region)
    lambdas = boto3.client("lambda", region_name=region)
    events = boto3.client("events", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    blockers: List[str] = []
    function_proofs: Dict[str, Any] = {}
    function_arns: Dict[str, str] = {}
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
        arn = str(config.get("FunctionArn") or "")
        function_arns[role] = arn
        if actual_git_sha != expected_git_sha:
            blockers.append(f"DEPLOY_GIT_SHA_MISMATCH:{logical_id}")
        if actual_template_sha != expected_template_sha256:
            blockers.append(f"DEPLOY_TEMPLATE_SHA_MISMATCH:{logical_id}")

        configuration_matches = True
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
            async_retry_policy: Dict[str, Any] = {}
            async_destination_config: Any = None
            async_destination_config_present = False
            configured_async_destinations: Dict[str, Dict[str, str]] = {}
            async_destination_config_valid = False
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
                        "TRAINER_LAMBDA_ASYNC_DESTINATION_CONFIG_INVALID:"
                        f"{exc}"
                    )
                if async_retry_policy != TRAINER_RETRY_POLICY:
                    configuration_matches = False
                    blockers.append(
                        "TRAINER_LAMBDA_ASYNC_RETRY_POLICY_MISMATCH:"
                        f"expected={TRAINER_RETRY_POLICY}:"
                        f"actual={async_retry_policy}"
                    )
                if configured_async_destinations:
                    configuration_matches = False
                    blockers.append(
                        "TRAINER_LAMBDA_ASYNC_DESTINATION_CONFIG_PRESENT"
                    )
            except Exception as exc:
                configuration_matches = False
                blockers.append(
                    f"TRAINER_LAMBDA_ASYNC_RETRY_POLICY_CHECK_FAILED:{exc}"
                )
            safe_environment_keys = (
                *TRAINER_REQUIRED_ENVIRONMENT,
                *TRAINER_EXPECTED_ENVIRONMENT.keys(),
                "INQSI_DEPLOY_GIT_SHA",
                "INQSI_DEPLOY_TEMPLATE_SHA256",
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

        function_proofs[logical_id] = {
            "role": role,
            "physicalId": physical_id,
            "functionArn": arn,
            "handler": config.get("Handler"),
            "runtime": config.get("Runtime"),
            "lastModified": config.get("LastModified"),
            "codeSha256": config.get("CodeSha256"),
            "version": config.get("Version"),
            "deployGitSha": actual_git_sha or None,
            "deployTemplateSha256": actual_template_sha or None,
            "identityMatches": actual_git_sha == expected_git_sha and actual_template_sha == expected_template_sha256,
            "configurationMatches": configuration_matches,
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

    schedule_proofs: Dict[str, Any] = {}
    for role, expected in EXPECTED_SCHEDULES.items():
        arn = function_arns.get(role)
        if not arn:
            blockers.append(f"SCHEDULE_TARGET_FUNCTION_MISSING:{role}")
            continue
        rule_names = _rule_names_for_target(events, arn)
        enabled_rules: List[Dict[str, Any]] = []
        for rule_name in rule_names:
            rule = events.describe_rule(Name=rule_name)
            targets = _targets_for_rule(events, rule_name)
            matching_targets = [
                target
                for target in targets
                if str(target.get("Arn") or "") == arn
            ]
            if rule.get("State") == "ENABLED" and matching_targets:
                enabled_rules.append({
                    "name": rule_name,
                    "state": rule.get("State"),
                    "schedule": rule.get("ScheduleExpression"),
                    "targetArns": sorted(str(target.get("Arn") or "") for target in targets),
                    "matchingTargets": matching_targets,
                })
        schedules = sorted(str(rule.get("schedule") or "") for rule in enabled_rules)
        if schedules != sorted(expected):
            blockers.append(f"SCHEDULE_MISMATCH:{role}:expected={expected}:actual={schedules}")
        trainer_retry_policy_matches = None
        trainer_dead_letter_absent = None
        trainer_invocation_inputs_match = None
        if role == "trainer":
            trainer_retry_policy_matches = bool(
                enabled_rules
                and all(
                    all(
                        (target.get("RetryPolicy") or {}) == TRAINER_RETRY_POLICY
                        for target in rule.get("matchingTargets") or []
                    )
                    for rule in enabled_rules
                )
            )
            trainer_dead_letter_absent = bool(
                enabled_rules
                and all(
                    all(
                        not bool(target.get("DeadLetterConfig"))
                        for target in rule.get("matchingTargets") or []
                    )
                    for rule in enabled_rules
                )
            )
            if not trainer_retry_policy_matches:
                blockers.append("TRAINER_EVENTBRIDGE_RETRY_POLICY_MISMATCH")
            if not trainer_dead_letter_absent:
                blockers.append("TRAINER_EVENTBRIDGE_FAILURE_DESTINATION_PRESENT")
            expected_invocations = sorted(
                (
                    str(invocation["schedule"]),
                    json.dumps(invocation["input"], sort_keys=True, separators=(",", ":")),
                )
                for invocation in TRAINER_EXPECTED_INVOCATIONS
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
            trainer_invocation_inputs_match = (
                not malformed_input
                and sorted(actual_invocations) == expected_invocations
            )
            if not trainer_invocation_inputs_match:
                blockers.append("TRAINER_EVENTBRIDGE_INVOCATION_INPUT_MISMATCH")
        schedule_proofs[role] = {
            "functionArn": arn,
            "enabledRules": enabled_rules,
            "expectedSchedules": expected,
            "exactMatch": schedules == sorted(expected),
            "retryPolicyMatches": trainer_retry_policy_matches,
            "deadLetterQueueAbsent": trainer_dead_letter_absent,
            "deliveryPolicyMatches": bool(
                trainer_retry_policy_matches and trainer_dead_letter_absent
            ) if role == "trainer" else None,
            "invocationInputsMatch": trainer_invocation_inputs_match,
            "sqsFailureDestinationRequired": False if role == "trainer" else None,
            "expectedRetryPolicy": (
                dict(TRAINER_RETRY_POLICY) if role == "trainer" else None
            ),
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

        for rule_name in _all_rule_names(events):
            rule = events.describe_rule(Name=rule_name)
            if rule.get("State") != "ENABLED":
                continue
            targets = _targets_for_rule(events, rule_name)
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
                    "state": rule.get("State"),
                    "schedule": rule.get("ScheduleExpression"),
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
        "functions": function_proofs,
        "trainerConfiguration": trainer_configuration,
        "artifactBucket": artifact_bucket_proof,
        "schedules": schedule_proofs,
        "enabledLegacyRules": alternate_writer_rules,
        "alternateWriterAuthority": {
            "canonicalWriterArns": sorted(canonical_writer_arns),
            "discoveredWriterFunctions": discovered_writer_functions,
            "enabledAlternateRules": alternate_writer_rules,
            "scanComplete": not any(
                blocker.startswith("MLB_ALTERNATE_WRITER_DISCOVERY_FAILED:")
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
    parser.add_argument("--output", default="runtime_reports/mlb_deploy_identity_latest.json")
    args = parser.parse_args()

    result = verify(
        stack_name=args.stack_name,
        region=args.region,
        expected_git_sha=args.expected_git_sha,
        expected_template_sha256=args.expected_template_sha256,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True:
        raise SystemExit("MLB deployment identity verification failed: " + json.dumps(result.get("blockers")))


if __name__ == "__main__":
    main()
