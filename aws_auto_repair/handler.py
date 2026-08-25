"""AWS-native, sport-isolated operational auto-repair.

This control plane is deliberately narrow. It may re-enable allowlisted EventBridge
rules and invoke the same fail-closed Lambda entry points that AWS already schedules.
It cannot update target code/configuration, write target tables, clear target leases,
rewrite locks/predictions, change promotion gates, or select winners.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


SPORT_NAME = os.getenv("SPORT_NAME", "").strip()
TARGET_STACK_NAME = os.getenv("TARGET_STACK_NAME", "").strip()
FUNCTION_NAME_PREFIX = os.getenv("FUNCTION_NAME_PREFIX", "").strip()
RULE_NAME_PREFIX = os.getenv("RULE_NAME_PREFIX", "").strip()
STATE_TABLE_NAME = os.getenv("REPAIR_STATE_TABLE", "").strip()
LEASE_SECONDS = int(os.getenv("REPAIR_LEASE_SECONDS", "960"))
NORMAL_COOLDOWN_SECONDS = int(os.getenv("REPAIR_NORMAL_COOLDOWN_SECONDS", "900"))
TRANSIENT_COOLDOWN_SECONDS = int(os.getenv("REPAIR_TRANSIENT_COOLDOWN_SECONDS", "1800"))
EXTERNAL_COOLDOWN_SECONDS = int(os.getenv("REPAIR_EXTERNAL_COOLDOWN_SECONDS", "21600"))
DATA_CONTRACT_COOLDOWN_SECONDS = int(
    os.getenv("REPAIR_DATA_CONTRACT_COOLDOWN_SECONDS", "21600")
)
METRIC_NAMESPACE = os.getenv("REPAIR_METRIC_NAMESPACE", "Inqsi/AutoRepair").strip()

BOTO_CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 5})
_CFN = None
_LAMBDA = None
_CLOUDWATCH = None
_EVENTS = None
_SQS = None
_TABLE = None

STABLE_STACK_STATUSES = {
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
    "IMPORT_COMPLETE",
}

SAFE_DEFERRED_STATUSES = {
    "HISTORICAL_ONLY",
    "TRAINING_DEFERRED_BACKFILL_NOT_READY",
    "TRAINING_RETRY_NOT_DUE",
    "DEFERRED_BBD_RATE_LIMIT",
    "DEFERRED_SHARED_QUOTA_RESERVE",
    "DEFERRED_QUOTA",
    "REJECTED_BY_GATE",
    "NO_GAMES",
    "COLLECTING",
    "CARD_ALREADY_PUBLISHED",
    "CARD_PUBLISHED",
    "HISTORICAL_READY",
    "READY",
}

# Every repair payload is static and reviewed. No caller may supply a target
# function name or arbitrary payload at runtime.
SPORT_CONFIGS: dict[str, dict[str, Any]] = {
    "mlb-auto": {
        "components": [
            {
                "name": "autonomous_cycle",
                "logical_id": "MLBAutoLLMFunction",
                "lookback_minutes": 20,
                "payload": {"mode": "autonomous_cycle"},
            },
        ],
        "rule_target_logical_ids": ["MLBAutoLLMFunction"],
    },
    "tennis": {
        "components": [
            {
                "name": "autonomous_controller",
                "logical_id": "TennisAutonomousControllerFunction",
                "lookback_minutes": 25,
                "payload": {"action": "autonomous_cycle"},
            },
        ],
        # The live pipeline is a rule target but is intentionally repaired by
        # the controller so collection and settlement remain one gated cycle.
        "rule_target_logical_ids": [
            "TennisAutonomousControllerFunction",
            "TennisLivePipelineFunction",
        ],
    },
    "soccer": {
        "components": [
            {
                "name": "inventory",
                "logical_id": "SoccerInventoryFunction",
                "lookback_minutes": 45,
                "payload": {"action": "fixture_inventory"},
            },
            {
                "name": "dispatch",
                "logical_id": "SoccerDispatchFunction",
                "lookback_minutes": 10,
                "payload": {"action": "adaptive_dispatch"},
            },
            {
                "name": "freeze",
                "logical_id": "SoccerFreezeFunction",
                "lookback_minutes": 10,
                "payload": {"action": "freeze_t45_training_and_t10_final_decision"},
            },
            {
                "name": "settlement",
                "logical_id": "SoccerSettlementFunction",
                "lookback_minutes": 20,
                "payload": {"action": "settle_all_active_soccer"},
            },
            {
                "name": "trainer",
                "logical_id": "SoccerTrainerFunction",
                "lookback_minutes": 780,
                "payload": {"action": "train_evaluate_promote"},
            },
            {
                "name": "llm_analyst",
                "logical_id": "SoccerLlmAnalystFunction",
                "lookback_minutes": 130,
                "payload": {"action": "analyze_soccer_learning"},
            },
            {
                "name": "historical",
                "logical_id": "SoccerHistoricalFunction",
                "lookback_minutes": 130,
                "payload": {"mode": "featured"},
            },
            {
                "name": "controller",
                "logical_id": "SoccerControllerFunction",
                "lookback_minutes": 45,
                "payload": {"action": "autonomous_cycle"},
            },
        ],
        "rule_target_logical_ids": [
            "SoccerCatalogFunction",
            "SoccerDispatchFunction",
            "SoccerInventoryFunction",
            "SoccerOutrightDispatchFunction",
            "SoccerFreezeFunction",
            "SoccerSettlementFunction",
            "SoccerTrainerFunction",
            "SoccerLlmAnalystFunction",
            "SoccerHistoricalFunction",
            "SoccerControllerFunction",
        ],
        "dlq": {
            "queue_logical_id": "SoccerCollectionDeadLetterQueue",
            "recovery_logical_id": "SoccerDlqRecoveryFunction",
            "component_name": "collection_dlq",
            "payload": {"action": "recover_collection_dlq", "max_messages": 5000},
        },
    },
    "nfl": {
        "components": [
            {
                "name": "autonomous_historical",
                "logical_id": "NflAutonomousFunction",
                "lookback_minutes": 10,
                "payload": {"action": "autonomous_tick"},
            },
            {
                "name": "live_cycle",
                "logical_id": "NflLiveFunction",
                "lookback_minutes": 15,
                "payload": {"action": "live_tick"},
            },
            {
                "name": "trainer",
                "logical_id": "NflTrainingFunction",
                "lookback_minutes": 1560,
                "payload": {"action": "train"},
            },
        ],
        "rule_target_logical_ids": [
            "NflAutonomousFunction",
            "NflLiveFunction",
            "NflTrainingFunction",
        ],
    },
}


class RepairConfigurationError(RuntimeError):
    """Raised when the repair stack is not safely scoped to its target."""


def _client(name: str) -> Any:
    global _CFN, _LAMBDA, _CLOUDWATCH, _EVENTS, _SQS
    if name == "cloudformation":
        if _CFN is None:
            _CFN = boto3.client(name, config=BOTO_CONFIG)
        return _CFN
    if name == "lambda":
        if _LAMBDA is None:
            _LAMBDA = boto3.client(name, config=BOTO_CONFIG)
        return _LAMBDA
    if name == "cloudwatch":
        if _CLOUDWATCH is None:
            _CLOUDWATCH = boto3.client(name, config=BOTO_CONFIG)
        return _CLOUDWATCH
    if name == "events":
        if _EVENTS is None:
            _EVENTS = boto3.client(name, config=BOTO_CONFIG)
        return _EVENTS
    if name == "sqs":
        if _SQS is None:
            _SQS = boto3.client(name, config=BOTO_CONFIG)
        return _SQS
    raise ValueError(name)


def _table() -> Any:
    global _TABLE
    if _TABLE is None:
        _TABLE = boto3.resource("dynamodb", config=BOTO_CONFIG).Table(STATE_TABLE_NAME)
    return _TABLE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 8)))
    if isinstance(value, Mapping):
        return {str(key): _ddb(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_ddb(item) for item in value if item is not None]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _safe_text(value: Any, limit: int = 4000) -> str:
    try:
        text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        text = str(value)
    return text[:limit]


def _validate_runtime() -> dict[str, Any]:
    missing = [
        name
        for name, value in (
            ("SPORT_NAME", SPORT_NAME),
            ("TARGET_STACK_NAME", TARGET_STACK_NAME),
            ("FUNCTION_NAME_PREFIX", FUNCTION_NAME_PREFIX),
            ("RULE_NAME_PREFIX", RULE_NAME_PREFIX),
            ("REPAIR_STATE_TABLE", STATE_TABLE_NAME),
        )
        if not value
    ]
    if missing:
        raise RepairConfigurationError("MISSING_ENVIRONMENT:" + ",".join(missing))
    config = SPORT_CONFIGS.get(SPORT_NAME)
    if config is None:
        raise RepairConfigurationError(f"UNSUPPORTED_SPORT:{SPORT_NAME}")
    if SPORT_NAME not in TARGET_STACK_NAME and not (
        SPORT_NAME == "mlb-auto" and "mlb-auto" in TARGET_STACK_NAME
    ):
        raise RepairConfigurationError(
            f"TARGET_STACK_SPORT_MISMATCH:{SPORT_NAME}:{TARGET_STACK_NAME}"
        )
    if not FUNCTION_NAME_PREFIX.startswith(TARGET_STACK_NAME):
        raise RepairConfigurationError(
            f"FUNCTION_PREFIX_NOT_TARGET_SCOPED:{FUNCTION_NAME_PREFIX}"
        )
    if not RULE_NAME_PREFIX.startswith(TARGET_STACK_NAME):
        raise RepairConfigurationError(
            f"RULE_PREFIX_NOT_TARGET_SCOPED:{RULE_NAME_PREFIX}"
        )
    return config


def _stack_state() -> dict[str, Any]:
    response = _client("cloudformation").describe_stacks(StackName=TARGET_STACK_NAME)
    stacks = response.get("Stacks") or []
    if len(stacks) != 1:
        raise RuntimeError(f"TARGET_STACK_NOT_UNIQUE:{TARGET_STACK_NAME}")
    stack = stacks[0]
    status = str(stack.get("StackStatus") or "UNKNOWN")
    return {
        "stack_name": TARGET_STACK_NAME,
        "stack_id": stack.get("StackId"),
        "status": status,
        "stable": status in STABLE_STACK_STATUSES,
        "status_reason": stack.get("StackStatusReason"),
    }


def _stack_resources() -> dict[str, dict[str, Any]]:
    client = _client("cloudformation")
    output: dict[str, dict[str, Any]] = {}
    token = None
    while True:
        kwargs: dict[str, Any] = {"StackName": TARGET_STACK_NAME}
        if token:
            kwargs["NextToken"] = token
        page = client.list_stack_resources(**kwargs)
        for row in page.get("StackResourceSummaries") or []:
            logical_id = str(row.get("LogicalResourceId") or "")
            if logical_id:
                output[logical_id] = dict(row)
        token = page.get("NextToken")
        if not token:
            return output


def _physical_resource(
    resources: Mapping[str, Mapping[str, Any]], logical_id: str, expected_type: str
) -> str:
    row = resources.get(logical_id)
    if not row:
        raise RepairConfigurationError(f"TARGET_RESOURCE_MISSING:{logical_id}")
    actual_type = str(row.get("ResourceType") or "")
    if actual_type != expected_type:
        raise RepairConfigurationError(
            f"TARGET_RESOURCE_TYPE_MISMATCH:{logical_id}:{actual_type}:{expected_type}"
        )
    status = str(row.get("ResourceStatus") or "")
    if status and not status.endswith("_COMPLETE"):
        raise RuntimeError(f"TARGET_RESOURCE_NOT_STABLE:{logical_id}:{status}")
    physical_id = str(row.get("PhysicalResourceId") or "")
    if not physical_id:
        raise RepairConfigurationError(f"TARGET_RESOURCE_PHYSICAL_ID_MISSING:{logical_id}")
    return physical_id


def _target_function(
    resources: Mapping[str, Mapping[str, Any]], logical_id: str
) -> dict[str, Any]:
    name = _physical_resource(resources, logical_id, "AWS::Lambda::Function")
    if not name.startswith(FUNCTION_NAME_PREFIX):
        raise RepairConfigurationError(
            f"TARGET_FUNCTION_PREFIX_VIOLATION:{logical_id}:{name}"
        )
    config = _client("lambda").get_function_configuration(FunctionName=name)
    arn = str(config.get("FunctionArn") or "")
    if not arn or not arn.rsplit(":", 1)[-1].startswith(FUNCTION_NAME_PREFIX):
        raise RepairConfigurationError(
            f"TARGET_FUNCTION_ARN_PREFIX_VIOLATION:{logical_id}:{arn}"
        )
    return {
        "logical_id": logical_id,
        "name": name,
        "arn": arn,
        "state": config.get("State"),
        "last_update_status": config.get("LastUpdateStatus"),
        "runtime": config.get("Runtime"),
        "timeout": config.get("Timeout"),
        "memory_size": config.get("MemorySize"),
    }


def _metric_sum(
    *, function_name: str, metric_name: str, lookback_minutes: int, observed: datetime
) -> tuple[float, str | None]:
    duration_seconds = max(60, lookback_minutes * 60)
    period = max(60, int(math.ceil(duration_seconds / 1440.0 / 60.0) * 60))
    response = _client("cloudwatch").get_metric_statistics(
        Namespace="AWS/Lambda",
        MetricName=metric_name,
        Dimensions=[{"Name": "FunctionName", "Value": function_name}],
        StartTime=observed - timedelta(minutes=lookback_minutes),
        EndTime=observed,
        Period=period,
        Statistics=["Sum"],
    )
    points = response.get("Datapoints") or []
    total = sum(float(point.get("Sum") or 0.0) for point in points)
    stamps = [
        point.get("Timestamp")
        for point in points
        if point.get("Timestamp") and float(point.get("Sum") or 0.0) > 0.0
    ]
    latest = max(stamps).astimezone(timezone.utc).isoformat() if stamps else None
    return total, latest


def _function_health(function: Mapping[str, Any], lookback_minutes: int) -> dict[str, Any]:
    if function.get("state") != "Active":
        return {
            "healthy": False,
            "reason": f"FUNCTION_STATE_{function.get('state')}",
            "lookback_minutes": lookback_minutes,
        }
    if function.get("last_update_status") not in {None, "Successful"}:
        return {
            "healthy": False,
            "reason": f"FUNCTION_UPDATE_{function.get('last_update_status')}",
            "lookback_minutes": lookback_minutes,
        }
    observed = _now()
    invocations, latest_invocation = _metric_sum(
        function_name=str(function["name"]),
        metric_name="Invocations",
        lookback_minutes=lookback_minutes,
        observed=observed,
    )
    errors, latest_error = _metric_sum(
        function_name=str(function["name"]),
        metric_name="Errors",
        lookback_minutes=lookback_minutes,
        observed=observed,
    )
    if invocations < 1:
        reason = "NO_RECENT_INVOCATION"
    elif errors > 0 and (
        not latest_invocation or not latest_error or latest_error >= latest_invocation
    ):
        reason = "RECENT_LAMBDA_ERRORS"
    elif errors > 0:
        reason = "RECOVERED_AFTER_ERROR"
    else:
        reason = "HEALTHY"
    return {
        "healthy": reason in {"HEALTHY", "RECOVERED_AFTER_ERROR"},
        "reason": reason,
        "lookback_minutes": lookback_minutes,
        "invocations": int(invocations),
        "errors": int(errors),
        "latest_invocation_metric_bucket_at": latest_invocation,
        "latest_error_metric_bucket_at": latest_error,
    }


def _unwrap_lambda_payload(payload: Any) -> tuple[Any, int | None]:
    if not isinstance(payload, Mapping):
        return payload, None
    status_code = payload.get("statusCode")
    body = payload.get("body")
    if body is not None:
        try:
            decoded = json.loads(body) if isinstance(body, str) else body
        except Exception:
            decoded = body
        return decoded, int(status_code) if status_code is not None else None
    return dict(payload), int(status_code) if status_code is not None else None


def _classification_text(payload: Any, function_error: Any = None) -> str:
    return (_safe_text(payload, 12000) + " " + str(function_error or "")).lower()


def classify_result(
    payload: Any, *, function_error: Any = None, status_code: int | None = None
) -> str:
    """Classify target results without ever changing target authority or gates."""
    text = _classification_text(payload, function_error)
    if any(
        token in text
        for token in (
            "executionleaseunavailable",
            "execution_lease_unavailable",
            "lease unavailable",
            "holds the execution lease",
            "concurrent execution",
        )
    ):
        return "PROTECTED_DEFERRED"
    if any(
        token in text
        for token in (
            "daily_token_quota",
            "too many tokens per day",
            "marketplace subscription",
            "deferred_bbd_rate_limit",
            "deferred_shared_quota_reserve",
            "provider_rate_limit",
            "throttlingexception",
            "http_429",
            '"statuscode":429',
            '"status_code":429',
        )
    ):
        return "EXTERNAL_BLOCKER"
    if any(
        token in text
        for token in (
            "data_contract",
            "kickoff_missing",
            "historical_schedule_event_ambiguous",
            "chronology",
            "three_source_game_coverage_incomplete",
            "training_eligible",
            "immutable evidence missing",
            "provider evidence missing",
        )
    ):
        return "DATA_CONTRACT_BLOCKER"
    if function_error:
        return "FAILED"
    if status_code is not None and not 200 <= status_code < 300:
        return "FAILED"
    if isinstance(payload, Mapping):
        status = str(payload.get("status") or "").upper()
        if status in SAFE_DEFERRED_STATUSES or status.startswith("DEFERRED_"):
            return "SAFE_DEFERRED"
        if payload.get("ok") is False:
            return "FAILED"
        if payload.get("ok") is True:
            return "SUCCESS"
    return "SUCCESS"


def _invoke(function: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    response = _client("lambda").invoke(
        FunctionName=str(function["name"]),
        InvocationType="RequestResponse",
        Payload=json.dumps(dict(payload), separators=(",", ":")).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8")
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        parsed = {"raw": raw[:4000]}
    unwrapped, status_code = _unwrap_lambda_payload(parsed)
    classification = classify_result(
        unwrapped,
        function_error=response.get("FunctionError"),
        status_code=status_code,
    )
    return {
        "classification": classification,
        "function_error": response.get("FunctionError"),
        "status_code": status_code,
        "executed_version": response.get("ExecutedVersion"),
        "response": _safe_text(unwrapped),
    }


def _conditional_failure(exc: ClientError) -> bool:
    return str((exc.response.get("Error") or {}).get("Code") or "") == (
        "ConditionalCheckFailedException"
    )


def _acquire_lease(holder: str, now_epoch: int) -> bool:
    try:
        _table().update_item(
            Key={"PK": f"LEASE#{SPORT_NAME}", "SK": "ACTIVE"},
            UpdateExpression=(
                "SET lease_until_epoch=:until, holder=:holder, acquired_at=:at, ttl=:ttl"
            ),
            ConditionExpression=(
                "attribute_not_exists(lease_until_epoch) OR lease_until_epoch < :now"
            ),
            ExpressionAttributeValues={
                ":until": now_epoch + LEASE_SECONDS,
                ":holder": holder,
                ":at": _iso(_now()),
                ":ttl": now_epoch + LEASE_SECONDS + 86400,
                ":now": now_epoch,
            },
        )
        return True
    except ClientError as exc:
        if _conditional_failure(exc):
            return False
        raise


def _release_lease(holder: str, now_epoch: int) -> None:
    try:
        _table().update_item(
            Key={"PK": f"LEASE#{SPORT_NAME}", "SK": "ACTIVE"},
            UpdateExpression=(
                "SET lease_until_epoch=:now, released_at=:at REMOVE holder"
            ),
            ConditionExpression="holder=:holder",
            ExpressionAttributeValues={
                ":now": now_epoch,
                ":at": _iso(_now()),
                ":holder": holder,
            },
        )
    except ClientError as exc:
        if not _conditional_failure(exc):
            raise


def _component_state(name: str) -> dict[str, Any]:
    response = _table().get_item(
        Key={"PK": f"COMPONENT#{SPORT_NAME}", "SK": name},
        ConsistentRead=True,
    )
    return _plain(response.get("Item") or {})


def _cooldown_ready(name: str, now_epoch: int) -> tuple[bool, dict[str, Any]]:
    state = _component_state(name)
    return now_epoch >= int(state.get("next_allowed_epoch") or 0), state


def _cooldown_seconds(classification: str) -> int:
    if classification == "EXTERNAL_BLOCKER":
        return EXTERNAL_COOLDOWN_SECONDS
    if classification == "DATA_CONTRACT_BLOCKER":
        return DATA_CONTRACT_COOLDOWN_SECONDS
    if classification in {"PROTECTED_DEFERRED", "FAILED"}:
        return TRANSIENT_COOLDOWN_SECONDS
    return NORMAL_COOLDOWN_SECONDS


def _record_component(
    *, name: str, classification: str, detail: Mapping[str, Any], now_epoch: int
) -> None:
    cooldown = _cooldown_seconds(classification)
    _table().put_item(
        Item=_ddb(
            {
                "PK": f"COMPONENT#{SPORT_NAME}",
                "SK": name,
                "last_classification": classification,
                "last_detail": dict(detail),
                "last_attempt_at": _iso(_now()),
                "next_allowed_epoch": now_epoch + cooldown,
                "ttl": now_epoch + 90 * 86400,
            }
        )
    )


def _target_rule_names(function_arn: str) -> Iterable[str]:
    client = _client("events")
    token = None
    while True:
        kwargs: dict[str, Any] = {"TargetArn": function_arn, "Limit": 100}
        if token:
            kwargs["NextToken"] = token
        page = client.list_rule_names_by_target(**kwargs)
        yield from [str(name) for name in page.get("RuleNames") or []]
        token = page.get("NextToken")
        if not token:
            break


def _repair_rules(
    *,
    config: Mapping[str, Any],
    resources: Mapping[str, Mapping[str, Any]],
    functions: Mapping[str, Mapping[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    stack_rule_names = {
        str(row.get("PhysicalResourceId") or "")
        for row in resources.values()
        if row.get("ResourceType") == "AWS::Events::Rule"
        and str(row.get("PhysicalResourceId") or "").startswith(RULE_NAME_PREFIX)
    }
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for logical_id in config.get("rule_target_logical_ids") or []:
        function = functions.get(str(logical_id))
        if not function:
            function = _target_function(resources, str(logical_id))
        for rule_name in _target_rule_names(str(function["arn"])):
            if rule_name in seen:
                continue
            seen.add(rule_name)
            if rule_name not in stack_rule_names or not rule_name.startswith(RULE_NAME_PREFIX):
                results.append(
                    {
                        "rule_name": rule_name,
                        "status": "SKIPPED_NOT_TARGET_STACK_RULE",
                    }
                )
                continue
            before = _client("events").describe_rule(Name=rule_name)
            state_before = str(before.get("State") or "UNKNOWN")
            if state_before == "DISABLED" and not dry_run:
                _client("events").enable_rule(Name=rule_name)
            after = _client("events").describe_rule(Name=rule_name)
            state_after = str(after.get("State") or "UNKNOWN")
            results.append(
                {
                    "rule_name": rule_name,
                    "state_before": state_before,
                    "state_after": state_after,
                    "repaired": state_before == "DISABLED" and state_after == "ENABLED",
                    "dry_run": dry_run,
                }
            )
    return results


def _queue_url(physical_id: str) -> str:
    if physical_id.startswith("https://"):
        return physical_id
    if not physical_id.startswith(TARGET_STACK_NAME):
        raise RepairConfigurationError(
            f"TARGET_QUEUE_PREFIX_VIOLATION:{physical_id}"
        )
    return str(_client("sqs").get_queue_url(QueueName=physical_id)["QueueUrl"])


def _queue_depth(queue_url: str) -> dict[str, int]:
    attrs = _client("sqs").get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    ).get("Attributes") or {}
    visible = int(attrs.get("ApproximateNumberOfMessages") or 0)
    inflight = int(attrs.get("ApproximateNumberOfMessagesNotVisible") or 0)
    delayed = int(attrs.get("ApproximateNumberOfMessagesDelayed") or 0)
    return {
        "visible": visible,
        "inflight": inflight,
        "delayed": delayed,
        "total": visible + inflight + delayed,
    }


def _repair_dlq(
    *,
    spec: Mapping[str, Any],
    resources: Mapping[str, Mapping[str, Any]],
    functions: Mapping[str, Mapping[str, Any]],
    now_epoch: int,
    dry_run: bool,
) -> dict[str, Any]:
    physical = _physical_resource(
        resources, str(spec["queue_logical_id"]), "AWS::SQS::Queue"
    )
    queue_url = _queue_url(physical)
    before = _queue_depth(queue_url)
    result: dict[str, Any] = {
        "component": spec["component_name"],
        "queue_url_suffix": queue_url.rsplit("/", 1)[-1],
        "before": before,
        "dry_run": dry_run,
    }
    if before["total"] <= 0:
        result["status"] = "HEALTHY_EMPTY"
        return result
    ready, cooldown = _cooldown_ready(str(spec["component_name"]), now_epoch)
    if not ready:
        result.update(
            {
                "status": "COOLDOWN_ACTIVE",
                "cooldown": cooldown,
            }
        )
        return result
    if dry_run:
        result["status"] = "WOULD_INVOKE_RECOVERY"
        return result
    function = functions.get(str(spec["recovery_logical_id"]))
    if not function:
        function = _target_function(resources, str(spec["recovery_logical_id"]))
    invocation = _invoke(function, spec["payload"])
    after = _queue_depth(queue_url)
    result.update(
        {
            "status": "RECOVERY_INVOKED",
            "invocation": invocation,
            "after": after,
        }
    )
    _record_component(
        name=str(spec["component_name"]),
        classification=str(invocation["classification"]),
        detail=result,
        now_epoch=now_epoch,
    )
    return result


def _persist_report(report: Mapping[str, Any], now_epoch: int) -> None:
    observed_at = str(report.get("observed_at") or _iso(_now()))
    item = {
        "PK": f"RUN#{SPORT_NAME}",
        "SK": observed_at,
        "report": dict(report),
        "ttl": now_epoch + 30 * 86400,
    }
    latest = {
        "PK": f"STATUS#{SPORT_NAME}",
        "SK": "LATEST",
        "report": dict(report),
        "updated_at": observed_at,
        "ttl": now_epoch + 90 * 86400,
    }
    _table().put_item(Item=_ddb(item))
    _table().put_item(Item=_ddb(latest))


def _publish_metrics(counts: Mapping[str, int]) -> None:
    data = []
    for name, value in counts.items():
        data.append(
            {
                "MetricName": name,
                "Dimensions": [{"Name": "Sport", "Value": SPORT_NAME}],
                "Unit": "Count",
                "Value": int(value),
            }
        )
    if data:
        _client("cloudwatch").put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=data,
        )


def _count_outcomes(
    component_results: Iterable[Mapping[str, Any]],
    dlq_result: Mapping[str, Any] | None,
    rule_results: Iterable[Mapping[str, Any]],
    dry_run: bool,
) -> dict[str, int]:
    classifications: list[str] = []
    attempts = 0
    for row in component_results:
        invocation = row.get("invocation")
        if isinstance(invocation, Mapping):
            attempts += 1
            classifications.append(str(invocation.get("classification") or "FAILED"))
    if dlq_result and isinstance(dlq_result.get("invocation"), Mapping):
        attempts += 1
        classifications.append(
            str((dlq_result.get("invocation") or {}).get("classification") or "FAILED")
        )
    successes = sum(
        value in {"SUCCESS", "SAFE_DEFERRED"} for value in classifications
    )
    return {
        "RepairAttempts": attempts,
        "RepairSuccesses": successes,
        "RepairFailures": sum(value == "FAILED" for value in classifications),
        "ExternalBlockers": sum(
            value == "EXTERNAL_BLOCKER" for value in classifications
        ),
        "DataContractBlockers": sum(
            value == "DATA_CONTRACT_BLOCKER" for value in classifications
        ),
        "ProtectedDeferrals": sum(
            value == "PROTECTED_DEFERRED" for value in classifications
        ),
        "RulesEnabled": sum(bool(row.get("repaired")) for row in rule_results),
        "DryRunCycles": int(dry_run),
    }


def run_cycle(event: Mapping[str, Any] | None = None) -> dict[str, Any]:
    event = dict(event or {})
    config = _validate_runtime()
    requested_sport = str(event.get("sport") or SPORT_NAME)
    if requested_sport != SPORT_NAME:
        raise RepairConfigurationError(
            f"EVENT_SPORT_MISMATCH:{requested_sport}:{SPORT_NAME}"
        )
    action = str(event.get("action") or "cycle").lower()
    if action != "cycle":
        raise RepairConfigurationError(f"UNSUPPORTED_ACTION:{action}")
    dry_run = bool(event.get("dry_run", False))
    now = _now()
    now_epoch = int(now.timestamp())
    holder = f"{SPORT_NAME}:{uuid.uuid4()}"
    if not _acquire_lease(holder, now_epoch):
        report = {
            "ok": True,
            "sport": SPORT_NAME,
            "target_stack": TARGET_STACK_NAME,
            "observed_at": _iso(now),
            "status": "REPAIR_LEASE_HELD",
            "repair_attempted": False,
            "immutable_prediction_history_rewritten": False,
            "promotion_gate_changed": False,
            "winner_authority_changed": False,
            "other_sport_changed": False,
        }
        _publish_metrics({"ProtectedDeferrals": 1})
        return report

    try:
        stack = _stack_state()
        if not stack["stable"]:
            report = {
                "ok": True,
                "sport": SPORT_NAME,
                "target_stack": TARGET_STACK_NAME,
                "observed_at": _iso(now),
                "status": "TARGET_STACK_BUSY_OR_UNSAFE",
                "stack": stack,
                "repair_attempted": False,
                "immutable_prediction_history_rewritten": False,
                "promotion_gate_changed": False,
                "winner_authority_changed": False,
                "other_sport_changed": False,
            }
            _persist_report(report, now_epoch)
            _publish_metrics({"ProtectedDeferrals": 1})
            return report

        resources = _stack_resources()
        functions: dict[str, dict[str, Any]] = {}
        required_function_ids = {
            str(row["logical_id"]) for row in config.get("components") or []
        }
        required_function_ids.update(
            str(item) for item in config.get("rule_target_logical_ids") or []
        )
        dlq_spec = config.get("dlq")
        if isinstance(dlq_spec, Mapping):
            required_function_ids.add(str(dlq_spec["recovery_logical_id"]))
        for logical_id in sorted(required_function_ids):
            functions[logical_id] = _target_function(resources, logical_id)

        rule_results = _repair_rules(
            config=config,
            resources=resources,
            functions=functions,
            dry_run=dry_run,
        )

        component_results: list[dict[str, Any]] = []
        any_repair_invoked = False
        for spec in config.get("components") or []:
            name = str(spec["name"])
            function = functions[str(spec["logical_id"])]
            health = _function_health(function, int(spec["lookback_minutes"]))
            row: dict[str, Any] = {
                "component": name,
                "function_logical_id": spec["logical_id"],
                "function_name": function["name"],
                "health_before": health,
                "dry_run": dry_run,
            }
            if health["healthy"]:
                row["status"] = "HEALTHY_NO_REPAIR"
                component_results.append(row)
                continue
            ready, cooldown = _cooldown_ready(name, now_epoch)
            if not ready:
                row.update({"status": "COOLDOWN_ACTIVE", "cooldown": cooldown})
                component_results.append(row)
                continue
            if dry_run:
                row["status"] = "WOULD_INVOKE_SAFE_ENTRYPOINT"
                component_results.append(row)
                continue
            invocation = _invoke(function, spec["payload"])
            any_repair_invoked = True
            row.update(
                {
                    "status": "SAFE_ENTRYPOINT_INVOKED",
                    "invocation": invocation,
                }
            )
            _record_component(
                name=name,
                classification=str(invocation["classification"]),
                detail=row,
                now_epoch=now_epoch,
            )
            component_results.append(row)

        dlq_result = None
        if isinstance(dlq_spec, Mapping):
            dlq_result = _repair_dlq(
                spec=dlq_spec,
                resources=resources,
                functions=functions,
                now_epoch=now_epoch,
                dry_run=dry_run,
            )
            any_repair_invoked = any_repair_invoked or bool(
                isinstance(dlq_result.get("invocation"), Mapping)
            )

        # Soccer's controller is observational. Refresh it once after another
        # component/DLQ repair so authority reflects the repaired runtime state.
        if SPORT_NAME == "soccer" and any_repair_invoked and not dry_run:
            controller = next(
                (
                    row
                    for row in config.get("components") or []
                    if row.get("name") == "controller"
                ),
                None,
            )
            if controller:
                invocation = _invoke(
                    functions[str(controller["logical_id"])], controller["payload"]
                )
                component_results.append(
                    {
                        "component": "controller_post_repair_refresh",
                        "function_logical_id": controller["logical_id"],
                        "function_name": functions[str(controller["logical_id"])]["name"],
                        "status": "POST_REPAIR_REFRESH_INVOKED",
                        "invocation": invocation,
                        "dry_run": False,
                    }
                )

        counts = _count_outcomes(
            component_results, dlq_result, rule_results, dry_run
        )
        unresolved = (
            counts["RepairFailures"]
            + counts["ExternalBlockers"]
            + counts["DataContractBlockers"]
        )
        report = {
            "ok": counts["RepairFailures"] == 0,
            "sport": SPORT_NAME,
            "target_stack": TARGET_STACK_NAME,
            "observed_at": _iso(now),
            "status": (
                "DRY_RUN"
                if dry_run
                else "REPAIRED_OR_HEALTHY"
                if unresolved == 0
                else "SAFE_REPAIR_COMPLETED_WITH_BLOCKERS"
            ),
            "stack": stack,
            "components": component_results,
            "rules": rule_results,
            "dlq": dlq_result,
            "metrics": counts,
            "repair_attempted": bool(counts["RepairAttempts"] or counts["RulesEnabled"]),
            "safe_aws_operations": [
                "events:EnableRule on target-stack rules only",
                "lambda:InvokeFunction on static target-stack entrypoints only",
                "sqs:GetQueueAttributes read-only",
                "cloudwatch read/write isolated repair metrics",
                "dynamodb writes to isolated repair state only",
            ],
            "forbidden_operations_available": False,
            "immutable_prediction_history_rewritten": False,
            "promotion_gate_changed": False,
            "winner_authority_changed": False,
            "other_sport_changed": False,
        }
        _persist_report(report, now_epoch)
        _publish_metrics(counts)
        return report
    finally:
        _release_lease(holder, int(time.time()))


def lambda_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    del context
    try:
        return run_cycle(event)
    except Exception as exc:
        now = _now()
        now_epoch = int(now.timestamp())
        report = {
            "ok": False,
            "sport": SPORT_NAME or "UNKNOWN",
            "target_stack": TARGET_STACK_NAME or "UNKNOWN",
            "observed_at": _iso(now),
            "status": "AUTO_REPAIR_CONTROL_PLANE_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "immutable_prediction_history_rewritten": False,
            "promotion_gate_changed": False,
            "winner_authority_changed": False,
            "other_sport_changed": False,
        }
        try:
            if STATE_TABLE_NAME:
                _persist_report(report, now_epoch)
            if METRIC_NAMESPACE and SPORT_NAME:
                _publish_metrics({"RepairFailures": 1})
        except Exception:
            pass
        raise
