"""AWS-native, sport-isolated operational auto-repair.

Only two target mutations are allowed: re-enable a canonical EventBridge rule,
and invoke an existing idempotent sport Lambda with its normal schedule payload.
The controller never writes sport predictions, locks, labels, models or authority.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

VERSION = "INQSI-AWS-NATIVE-AUTO-REPAIR-v1"
SPORT = os.environ["SPORT_NAME"].lower().strip()
TARGET_STACK = os.environ["TARGET_STACK_NAME"].strip()
FUNCTION_PREFIX = os.environ["FUNCTION_NAME_PREFIX"].strip()
RULE_PREFIX = os.environ["RULE_NAME_PREFIX"].strip()
STATE_TABLE = os.environ["REPAIR_STATE_TABLE"].strip()
LEASE_SECONDS = max(60, int(os.getenv("REPAIR_LEASE_SECONDS", "960")))
NORMAL_COOLDOWN = max(60, int(os.getenv("REPAIR_NORMAL_COOLDOWN_SECONDS", "900")))
TRANSIENT_COOLDOWN = max(60, int(os.getenv("REPAIR_TRANSIENT_COOLDOWN_SECONDS", "1800")))
EXTERNAL_COOLDOWN = max(300, int(os.getenv("REPAIR_EXTERNAL_COOLDOWN_SECONDS", "21600")))
DATA_COOLDOWN = max(300, int(os.getenv("REPAIR_DATA_CONTRACT_COOLDOWN_SECONDS", "21600")))
METRIC_NAMESPACE = os.getenv("REPAIR_METRIC_NAMESPACE", "Inqsi/AutoRepair")

CFG = Config(retries={"mode": "adaptive", "max_attempts": 5})
CFN = boto3.client("cloudformation", config=CFG)
LAMBDA = boto3.client("lambda", config=CFG)
CW = boto3.client("cloudwatch", config=CFG)
EVENTS = boto3.client("events", config=CFG)
SQS = boto3.client("sqs", config=CFG)
TABLE = boto3.resource("dynamodb", config=CFG).Table(STATE_TABLE)

# name, logical id, normal schedule payload, liveness window, advisory, scheduled
SPORT_CONFIGS: Mapping[str, tuple[tuple[Any, ...], ...]] = {
    "mlb-core": (
        ("audited_pull", "MLBAuditedPullFunction", {"sport": "mlb", "t": "HOT", "run": "aws_native_auto_repair", "days_ahead": 0}, 35, False, True),
        ("daily_lock", "MLBDailyPickLockFunction", {"sport": "mlb", "run": "daily_lock_check", "auto_ingest": False}, 8, False, True),
        ("settlement", "MLBResultsSchedulerFunction", {"sport": "mlb", "days_from": 3, "run": "results_pull_15m"}, 35, False, True),
        ("selection_capture", "MLBMLTrainingFunction", {"sport": "mlb", "mode": "selection_capture", "run": "aws_native_prospective_selection_capture"}, 35, False, True),
    ),
    "mlb-auto": (
        ("autonomous_cycle", "MLBAutoLLMFunction", {"mode": "autonomous_cycle", "repair_source": "aws_native_auto_repair"}, 18, False, True),
    ),
    "tennis": (
        ("autonomous_controller", "TennisAutonomousControllerFunction", {"action": "autonomous_cycle", "repair_source": "aws_native_auto_repair"}, 35, False, True),
    ),
    "soccer": (
        ("inventory", "SoccerInventoryFunction", {"action": "fixture_inventory"}, 45, False, True),
        ("dispatch", "SoccerDispatchFunction", {"action": "adaptive_dispatch"}, 12, False, True),
        ("freeze", "SoccerFreezeFunction", {"action": "freeze_t45_training_and_t10_final_decision"}, 12, False, True),
        ("settlement", "SoccerSettlementFunction", {"action": "settle_all_active_soccer"}, 25, False, True),
        ("trainer", "SoccerTrainerFunction", {"action": "train_evaluate_promote"}, 780, False, True),
        ("llm_analyst", "SoccerLlmAnalystFunction", {"action": "analyze_soccer_learning"}, 130, True, True),
        ("historical", "SoccerHistoricalFunction", {"mode": "featured", "repair_source": "aws_native_auto_repair"}, 130, True, True),
        ("controller", "SoccerControllerFunction", {"action": "autonomous_cycle", "repair_source": "aws_native_auto_repair"}, 35, False, True),
        ("dlq_recovery", "SoccerDlqRecoveryFunction", {"action": "recover_collection_dlq", "max_messages": 5000}, 0, False, False),
    ),
    "nfl": (
        ("autonomous_tick", "NflAutonomousFunction", {"action": "autonomous_tick", "repair_source": "aws_native_auto_repair"}, 35, False, True),
    ),
}

LEASE_MARKERS = ("executionleaseunavailable", "execution_lease_unavailable", "lease unavailable", "lease held", "overlap_skipped")
EXTERNAL_MARKERS = ("daily_token_quota", "too many tokens per day", "throttlingexception", "deferred_bbd_rate_limit", "deferred_shared_quota_reserve", "shared_quota_reserve", "provider_rate_limit", "too many requests", "service unavailable")
DATA_MARKERS = ("bbd_kickoff_missing", "nfl_team_unrecognized", "authoritative kickoff", "missing authoritative", "source contract", "three_source_game_coverage_incomplete", "authoritative_card_deadline_missed", "no_future_pre_cutoff_slate")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now()).astimezone(timezone.utc).isoformat()


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 8)))
    if isinstance(value, Mapping):
        return {str(k): ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [ddb(v) for v in value if v is not None]
    return value


def conditional(exc: ClientError) -> bool:
    return str((exc.response.get("Error") or {}).get("Code") or "") == "ConditionalCheckFailedException"


def acquire(owner: str, observed: datetime) -> bool:
    current = int(observed.timestamp())
    expires = current + LEASE_SECONDS
    try:
        TABLE.put_item(
            Item={"PK": "LEASE", "SK": "GLOBAL", "owner": owner, "expires_at_epoch": expires, "ttl": expires + 86400},
            ConditionExpression="attribute_not_exists(PK) OR expires_at_epoch < :now",
            ExpressionAttributeValues={":now": current},
        )
        return True
    except ClientError as exc:
        if conditional(exc):
            return False
        raise


def release(owner: str) -> None:
    try:
        TABLE.delete_item(
            Key={"PK": "LEASE", "SK": "GLOBAL"},
            ConditionExpression="#owner=:owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )
    except ClientError as exc:
        if not conditional(exc):
            raise


def get_state(pk: str) -> dict[str, Any]:
    return plain(TABLE.get_item(Key={"PK": pk, "SK": "STATE"}, ConsistentRead=True).get("Item") or {})


def put_component(name: str, classification: str, next_at: datetime, evidence: str, function_name: str | None = None) -> None:
    TABLE.put_item(Item=ddb({
        "PK": f"COMPONENT#{name}", "SK": "STATE", "checked_at": iso(),
        "classification": classification, "next_attempt_at": iso(next_at),
        "next_attempt_epoch": int(next_at.timestamp()), "last_evidence": evidence[:4000],
        "last_function_name": function_name,
    }))


def persist(result: Mapping[str, Any]) -> None:
    stamp = now()
    ttl = int((stamp + timedelta(days=30)).timestamp())
    run_id = str(result.get("run_id") or uuid.uuid4().hex)
    TABLE.put_item(Item=ddb({"PK": "RUN", "SK": f"{iso(stamp)}#{run_id}", "ttl": ttl, **dict(result)}))
    TABLE.put_item(Item=ddb({"PK": "STATE", "SK": "STATE", "ttl": ttl, **dict(result)}))


def resolve(logical_id: str) -> dict[str, Any]:
    detail = CFN.describe_stack_resource(StackName=TARGET_STACK, LogicalResourceId=logical_id).get("StackResourceDetail") or {}
    name = str(detail.get("PhysicalResourceId") or "")
    if not name.startswith(FUNCTION_PREFIX):
        raise RuntimeError(f"TARGET_FUNCTION_ISOLATION_VIOLATION:{logical_id}:{name}")
    config = LAMBDA.get_function_configuration(FunctionName=name)
    arn = str(config.get("FunctionArn") or "")
    if f":function:{FUNCTION_PREFIX}" not in arn:
        raise RuntimeError(f"TARGET_FUNCTION_ARN_ISOLATION_VIOLATION:{logical_id}:{arn}")
    return {"name": name, "arn": arn, "environment": ((config.get("Environment") or {}).get("Variables") or {})}


def metric(name: str, metric_name: str, minutes: int, observed: datetime) -> tuple[int, str | None]:
    page = CW.get_metric_statistics(
        Namespace="AWS/Lambda", MetricName=metric_name,
        Dimensions=[{"Name": "FunctionName", "Value": name}],
        StartTime=observed - timedelta(minutes=minutes), EndTime=observed,
        Period=max(60, min(300, minutes * 60)), Statistics=["Sum"],
    )
    points = page.get("Datapoints") or []
    total = int(sum(float(row.get("Sum") or 0) for row in points))
    timestamps = [row["Timestamp"] for row in points if float(row.get("Sum") or 0) > 0 and row.get("Timestamp")]
    return total, max(timestamps).astimezone(timezone.utc).isoformat() if timestamps else None


def health(name: str, minutes: int, observed: datetime) -> dict[str, Any]:
    invocations, latest = metric(name, "Invocations", minutes, observed)
    errors, latest_error = metric(name, "Errors", minutes, observed)
    if invocations < 1:
        reason = "NO_RECENT_INVOCATION"
    elif errors and (not latest or not latest_error or latest_error >= latest):
        reason = "RECENT_UNRECOVERED_ERROR"
    elif errors:
        reason = "RECOVERED_AFTER_ERROR"
    else:
        reason = "HEALTHY"
    return {"healthy": reason in {"HEALTHY", "RECOVERED_AFTER_ERROR"}, "reason": reason, "lookback_minutes": minutes, "invocations": invocations, "errors": errors, "latest_invocation_at": latest, "latest_error_at": latest_error}


def repair_rules(function_arn: str, dry_run: bool) -> dict[str, Any]:
    names: list[str] = []
    token = None
    while True:
        kwargs: dict[str, Any] = {"TargetArn": function_arn, "Limit": 100}
        if token:
            kwargs["NextToken"] = token
        page = EVENTS.list_rule_names_by_target(**kwargs)
        names += [str(v) for v in page.get("RuleNames") or []]
        token = page.get("NextToken")
        if not token:
            break
    canonical = sorted(v for v in names if v.startswith(RULE_PREFIX))
    enabled: list[str] = []
    for rule_name in canonical:
        state = str(EVENTS.describe_rule(Name=rule_name).get("State") or "")
        targets = EVENTS.list_targets_by_rule(Rule=rule_name).get("Targets") or []
        if not any(str(row.get("Arn") or "") == function_arn for row in targets):
            raise RuntimeError(f"CANONICAL_RULE_TARGET_MISMATCH:{rule_name}")
        if state == "DISABLED":
            if not dry_run:
                EVENTS.enable_rule(Name=rule_name)
            enabled.append(rule_name)
        elif state != "ENABLED":
            raise RuntimeError(f"CANONICAL_RULE_STATE_INVALID:{rule_name}:{state}")
    return {"canonical_rules": canonical, "enabled_rules": enabled, "noncanonical_rules_ignored": sorted(set(names) - set(canonical))}


def unwrap(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"raw": value}
    result = dict(value)
    if "statusCode" not in result or "body" not in result:
        return result
    body = result.get("body")
    try:
        parsed = json.loads(body) if isinstance(body, str) else dict(body or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = {"raw_body": body}
    parsed["http_status_code"] = int(result.get("statusCode") or 0)
    return parsed


def invoke(name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    response = LAMBDA.invoke(FunctionName=name, InvocationType="RequestResponse", Payload=json.dumps(dict(payload), separators=(",", ":")).encode())
    result = unwrap(json.loads(response["Payload"].read().decode() or "{}"))
    if response.get("FunctionError"):
        raise RuntimeError("TARGET_FUNCTION_ERROR:" + json.dumps(result, sort_keys=True, default=str)[:1500])
    return result


def classify(result: Mapping[str, Any] | None = None, error: Exception | None = None) -> tuple[str, int, str]:
    text = json.dumps(result or {}, sort_keys=True, default=str) + (f" {error}" if error else "")
    lowered = text.lower()
    if any(v in lowered for v in LEASE_MARKERS):
        return "DEFERRED_ACTIVE_LEASE", TRANSIENT_COOLDOWN, text
    if any(v in lowered for v in EXTERNAL_MARKERS):
        return "DEFERRED_EXTERNAL_CAPACITY", EXTERNAL_COOLDOWN, text
    if any(v in lowered for v in DATA_MARKERS):
        return "BLOCKED_AUTHORITATIVE_DATA_CONTRACT", DATA_COOLDOWN, text
    if error or int((result or {}).get("http_status_code") or 200) >= 400 or (result or {}).get("ok") is False:
        return "FAILED", NORMAL_COOLDOWN, text
    return "SUCCEEDED", NORMAL_COOLDOWN, text


def cooldown(name: str, observed: datetime) -> tuple[bool, dict[str, Any]]:
    state = get_state(f"COMPONENT#{name}")
    return int(observed.timestamp()) < int(state.get("next_attempt_epoch") or 0), state


def dlq_depth(function: Mapping[str, Any]) -> int:
    url = str((function.get("environment") or {}).get("SOCCER_AUTO_COLLECTION_DLQ_URL") or "")
    if not url:
        raise RuntimeError("SOCCER_DLQ_URL_NOT_CONFIGURED")
    attrs = SQS.get_queue_attributes(QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed"]).get("Attributes") or {}
    return sum(int(attrs.get(v) or 0) for v in ("ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed"))


def put_metrics(metrics: Mapping[str, float]) -> None:
    CW.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=[{
        "MetricName": name, "Dimensions": [{"Name": "Sport", "Value": SPORT}],
        "Timestamp": now(), "Value": float(value), "Unit": "Count",
    } for name, value in metrics.items()])


def attempt(component: tuple[Any, ...], observed: datetime, dry_run: bool) -> tuple[dict[str, Any], bool, dict[str, float]]:
    name, logical_id, payload, minutes, advisory, scheduled = component
    detail: dict[str, Any] = {"name": name, "logical_id": logical_id, "advisory": advisory}
    counts = {"attempts": 0.0, "successes": 0.0, "failures": 0.0, "deferred": 0.0, "enabled": 0.0, "unhealthy": 0.0}
    okay = True
    try:
        function = resolve(logical_id)
        detail["function_name"] = function["name"]
        if not scheduled:
            depth = dlq_depth(function)
            detail["dlq_depth"] = depth
            needs_repair = depth > 0
        else:
            schedules = repair_rules(function["arn"], dry_run)
            if not schedules["canonical_rules"]:
                raise RuntimeError(f"CANONICAL_SCHEDULE_NOT_FOUND:{logical_id}")
            detail["schedules"] = schedules
            counts["enabled"] += len(schedules["enabled_rules"])
            state = health(function["name"], minutes, observed)
            detail["health_before"] = state
            needs_repair = not state["healthy"] or bool(schedules["enabled_rules"])
            counts["unhealthy"] += 0 if state["healthy"] else 1
        active, prior = cooldown(name, observed)
        if needs_repair and active:
            detail["repair"] = {"status": "COOLDOWN_ACTIVE", "next_attempt_at": prior.get("next_attempt_at"), "last_classification": prior.get("classification")}
            counts["deferred"] += 1
        elif needs_repair and dry_run:
            detail["repair"] = {"status": "DRY_RUN"}
        elif needs_repair:
            counts["attempts"] += 1
            result = None
            error = None
            try:
                result = invoke(function["name"], payload)
            except Exception as exc:
                error = exc
            classification, seconds, evidence = classify(result, error)
            next_at = observed + timedelta(seconds=seconds)
            put_component(name, classification, next_at, evidence, function["name"])
            detail["repair"] = {"status": classification, "result": result, "error": str(error)[:1500] if error else None, "next_attempt_at": iso(next_at)}
            if classification == "SUCCEEDED":
                counts["successes"] += 1
            elif classification.startswith(("DEFERRED_", "BLOCKED_")):
                counts["deferred"] += 1
                okay = advisory or not classification.startswith("BLOCKED_")
            else:
                counts["failures"] += 1
                okay = advisory
        else:
            detail["repair"] = {"status": "NOT_REQUIRED"}
    except Exception as exc:
        classification, seconds, evidence = classify(error=exc)
        next_at = observed + timedelta(seconds=seconds)
        put_component(name, classification, next_at, evidence)
        detail["error"] = str(exc)[:1500]
        detail["repair"] = {"status": classification, "next_attempt_at": iso(next_at)}
        counts["failures"] += 1
        okay = advisory
    return detail, okay, counts


def run_cycle(dry_run: bool = False) -> dict[str, Any]:
    observed = now()
    run_id = uuid.uuid4().hex
    owner = f"{run_id}:{uuid.uuid4().hex[:8]}"
    if SPORT not in SPORT_CONFIGS:
        raise RuntimeError(f"UNSUPPORTED_SPORT_CONFIG:{SPORT}")
    if not acquire(owner, observed):
        result = safety_result(run_id, observed, "DEFERRED_ACTIVE_REPAIR_LEASE", True, dry_run, [])
        persist(result)
        put_metrics({"RepairDeferred": 1})
        return result
    try:
        stack_status = str((CFN.describe_stacks(StackName=TARGET_STACK).get("Stacks") or [{}])[0].get("StackStatus") or "UNKNOWN")
        if stack_status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
            result = safety_result(run_id, observed, "TARGET_STACK_NOT_STABLE", False, dry_run, [], stack_status)
            persist(result)
            put_metrics({"Checks": 1, "RepairFailures": 1})
            return result
        components: list[dict[str, Any]] = []
        overall = True
        metrics = {"Checks": 1.0, "RepairAttempts": 0.0, "RepairSuccesses": 0.0, "RepairFailures": 0.0, "RepairDeferred": 0.0, "SchedulesEnabled": 0.0, "UnhealthyComponents": 0.0}
        for component in SPORT_CONFIGS[SPORT]:
            detail, okay, counts = attempt(component, observed, dry_run)
            components.append(detail)
            overall = overall and okay
            metrics["RepairAttempts"] += counts["attempts"]
            metrics["RepairSuccesses"] += counts["successes"]
            metrics["RepairFailures"] += counts["failures"]
            metrics["RepairDeferred"] += counts["deferred"]
            metrics["SchedulesEnabled"] += counts["enabled"]
            metrics["UnhealthyComponents"] += counts["unhealthy"]
        result = safety_result(run_id, observed, "HEALTHY_OR_REPAIRED" if overall else "DEGRADED_FAIL_CLOSED", overall, dry_run, components, stack_status)
        result["metrics"] = metrics
        persist(result)
        put_metrics(metrics)
        return result
    finally:
        release(owner)


def safety_result(run_id: str, observed: datetime, status: str, okay: bool, dry_run: bool, components: list[dict[str, Any]], stack_status: str | None = None) -> dict[str, Any]:
    return {
        "ok": okay, "version": VERSION, "sport": SPORT, "run_id": run_id,
        "checked_at": iso(observed), "status": status, "target_stack": TARGET_STACK,
        "target_stack_status": stack_status, "dry_run": dry_run, "components": components,
        "production_authority_changed": False, "direct_sport_table_writes": False,
        "post_start_prediction_creation_allowed": False,
        "immutable_prediction_rewrite_allowed": False,
        "execution_lease_bypass_allowed": False, "gate_relaxation_allowed": False,
    }


def lambda_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    del context
    request = dict(event or {})
    action = str(request.get("action") or "cycle").lower().strip()
    if action == "status":
        return {"ok": True, "version": VERSION, "sport": SPORT, "target_stack": TARGET_STACK, "state": get_state("STATE"), "read_only": True}
    if action not in {"cycle", "audit"}:
        return {"ok": False, "version": VERSION, "sport": SPORT, "error": f"UNSUPPORTED_ACTION:{action}"}
    return run_cycle(dry_run=bool(request.get("dry_run")) or action == "audit")


# Stable test/diagnostic aliases; these do not add runtime authority.
_classification = classify
_unwrap_lambda_payload = unwrap
_repair_schedules = repair_rules
_invoke = invoke
TRANSIENT_COOLDOWN_SECONDS = TRANSIENT_COOLDOWN
EXTERNAL_COOLDOWN_SECONDS = EXTERNAL_COOLDOWN
DATA_CONTRACT_COOLDOWN_SECONDS = DATA_COOLDOWN


def _resolve_function(component: Any) -> dict[str, Any]:
    logical_id = component[1] if isinstance(component, tuple) else component.logical_id
    return resolve(str(logical_id))
