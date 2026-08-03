from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
LIVE_FUNCTION = os.environ["TENNIS_LIVE_FUNCTION"]
BACKFILL_FUNCTION = os.environ["TENNIS_BACKFILL_FUNCTION"]
MIN_TRAINING_SAMPLES = int(os.getenv("TENNIS_MIN_TRAINING_SAMPLES", "100"))
MIN_LIVE_AUDIT = int(os.getenv("TENNIS_MIN_LIVE_AUDIT", "30"))
MIN_LIVE_ACCURACY = float(os.getenv("TENNIS_MIN_LIVE_ACCURACY", "0.55"))
MAX_LIVE_BRIER = float(os.getenv("TENNIS_MAX_LIVE_BRIER", "0.25"))
MAX_CONSECUTIVE_FAILURES = int(os.getenv("TENNIS_MAX_CONSECUTIVE_FAILURES", "3"))
INVOKE_MAX_ATTEMPTS = max(1, int(os.getenv("TENNIS_INVOKE_MAX_ATTEMPTS", "4")))
INVOKE_BASE_DELAY_SECONDS = max(
    0.1, float(os.getenv("TENNIS_INVOKE_BASE_DELAY_SECONDS", "2"))
)
INVOKE_MAX_DELAY_SECONDS = max(
    INVOKE_BASE_DELAY_SECONDS,
    float(os.getenv("TENNIS_INVOKE_MAX_DELAY_SECONDS", "20")),
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


HISTORICAL_ENABLED = _env_bool("TENNIS_HISTORICAL_ENABLED", False)
HISTORICAL_REQUIRED = _env_bool("TENNIS_HISTORICAL_REQUIRED", False)

table = boto3.resource("dynamodb").Table(TABLE_NAME)
lambda_client = boto3.client(
    "lambda",
    config=Config(retries={"mode": "adaptive", "max_attempts": 3}),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 8)))


def _retryable_invoke_error(exc: ClientError) -> bool:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error") or {}
    metadata = response.get("ResponseMetadata") or {}
    code = str(error.get("Code") or "")
    status = int(metadata.get("HTTPStatusCode") or 0)
    return code in {
        "TooManyRequestsException",
        "ServiceException",
        "EC2ThrottledException",
        "RequestLimitExceeded",
        "Throttling",
        "ThrottlingException",
    } or status in {429, 500, 502, 503, 504}


def _invoke(function_name: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    for attempt in range(1, INVOKE_MAX_ATTEMPTS + 1):
        try:
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(dict(payload)).encode("utf-8"),
            )
        except ClientError as exc:
            if attempt >= INVOKE_MAX_ATTEMPTS or not _retryable_invoke_error(exc):
                raise
            base_delay = min(
                INVOKE_MAX_DELAY_SECONDS,
                INVOKE_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
            )
            jittered_delay = base_delay * (0.75 + random.random() * 0.5)
            time.sleep(jittered_delay)
            continue

        body = json.loads(response["Payload"].read().decode("utf-8") or "{}")
        if response.get("FunctionError"):
            raise RuntimeError(f"{function_name} failed: {body}")
        if isinstance(body, dict) and "body" in body:
            try:
                return json.loads(body["body"])
            except (TypeError, json.JSONDecodeError):
                return body
        return body if isinstance(body, dict) else {"result": body}

    raise RuntimeError(f"{function_name} invoke retry loop exited unexpectedly")


def _scan(prefix: str) -> Iterable[Dict[str, Any]]:
    kwargs: Dict[str, Any] = {"FilterExpression": Attr("PK").begins_with(prefix)}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        key = page.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key


def _live_audit(limit: int = 200) -> Dict[str, Any]:
    predictions: Dict[str, Dict[str, Any]] = {}
    for item in _scan("PREDICTION#"):
        match_id = str(item["PK"]).split("#", 1)[1]
        if match_id.startswith(("hist-", "bootstrap:")):
            continue
        current = predictions.get(match_id)
        if current is None or str(item.get("SK", "")) > str(current.get("SK", "")):
            predictions[match_id] = item

    settlements: Dict[str, Dict[str, Any]] = {}
    for item in _scan("SETTLEMENT#"):
        match_id = str(item["PK"]).split("#", 1)[1]
        if match_id.startswith(("hist-", "bootstrap:")):
            continue
        settlements[match_id] = item

    rows = []
    for match_id, pred in predictions.items():
        settled = settlements.get(match_id)
        if not settled:
            continue
        try:
            probability = float(pred["probability"])
            label = int(settled["label"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((str(pred.get("SK", "")), probability, label))
    rows.sort(reverse=True)
    rows = rows[:limit]
    if not rows:
        return {"count": 0, "accuracy": None, "brier": None}
    correct = sum(int((p >= 0.5) == bool(y)) for _, p, y in rows)
    brier = sum((p - y) ** 2 for _, p, y in rows) / len(rows)
    return {"count": len(rows), "accuracy": correct / len(rows), "brier": brier}


def _model_state() -> Dict[str, Any]:
    return table.get_item(
        Key={"PK": "MODEL", "SK": "STATE"}, ConsistentRead=True
    ).get("Item", {})


def _previous_state() -> Dict[str, Any]:
    return table.get_item(
        Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True
    ).get("Item", {})


def _authority(
    model: Mapping[str, Any], audit: Mapping[str, Any], failures: int
) -> tuple[str, str]:
    samples = int(model.get("training_samples", 0))
    if failures >= MAX_CONSECUTIVE_FAILURES:
        return "DEGRADED", "pipeline_failure_circuit_breaker"
    if samples < MIN_TRAINING_SAMPLES:
        return "SHADOW", "insufficient_total_training_samples"
    if int(audit.get("count") or 0) < MIN_LIVE_AUDIT:
        return "SHADOW", "insufficient_live_audit_samples"
    if float(audit.get("accuracy") or 0.0) < MIN_LIVE_ACCURACY:
        return "SHADOW", "live_accuracy_below_gate"
    if float(audit.get("brier") or 1.0) > MAX_LIVE_BRIER:
        return "SHADOW", "live_calibration_below_gate"
    return "AUTHORITATIVE", "all_autonomous_promotion_gates_passed"


def _run_action(
    actions: Dict[str, Any],
    failures: list[str],
    warnings: list[str],
    name: str,
    function_name: str,
    payload: Mapping[str, Any],
    *,
    required: bool = True,
) -> None:
    try:
        actions[name] = _invoke(function_name, payload)
    except Exception as exc:
        detail = f"{name}: {exc}"
        actions[name] = {"error": str(exc), "required": required}
        (failures if required else warnings).append(detail)


def run_cycle() -> Dict[str, Any]:
    previous = _previous_state()
    actions: Dict[str, Any] = {}
    failures: list[str] = []
    warnings: list[str] = []

    # Live collection and live settlement are promotion-critical. Historical
    # bootstrap is optional unless explicitly enabled and required. A missing
    # third-party historical source must never disable genuine live learning.
    _run_action(
        actions,
        failures,
        warnings,
        "collect",
        LIVE_FUNCTION,
        {"action": "collect"},
    )
    _run_action(
        actions,
        failures,
        warnings,
        "settle",
        LIVE_FUNCTION,
        {"action": "settle"},
    )

    if HISTORICAL_ENABLED:
        _run_action(
            actions,
            failures,
            warnings,
            "backfill",
            BACKFILL_FUNCTION,
            {"action": "backfill"},
            required=HISTORICAL_REQUIRED,
        )
    else:
        actions["backfill"] = {
            "skipped": True,
            "reason": "historical_backfill_disabled",
            "required": HISTORICAL_REQUIRED,
        }
        if HISTORICAL_REQUIRED:
            failures.append("backfill: historical backfill is required but disabled")

    consecutive_failures = (
        int(previous.get("consecutive_failures", 0)) + 1 if failures else 0
    )
    model = _model_state()
    audit = _live_audit()
    authority, reason = _authority(model, audit, consecutive_failures)
    now = _now()
    item = {
        "PK": "AUTONOMY",
        "SK": "STATE",
        "authority": authority,
        "reason": reason,
        "automatic_prediction_allowed": authority == "AUTHORITATIVE",
        "consecutive_failures": consecutive_failures,
        "model_version": int(model.get("version", 0)),
        "training_samples": int(model.get("training_samples", 0)),
        "live_audit_count": int(audit.get("count") or 0),
        "live_accuracy": _decimal(float(audit["accuracy"]))
        if audit.get("accuracy") is not None
        else None,
        "live_brier": _decimal(float(audit["brier"]))
        if audit.get("brier") is not None
        else None,
        "historical_backfill_enabled": HISTORICAL_ENABLED,
        "historical_backfill_required": HISTORICAL_REQUIRED,
        "last_cycle_at": now,
        "last_error": " | ".join(failures)[:4000] if failures else None,
        "last_warning": " | ".join(warnings)[:4000] if warnings else None,
        "actions": json.dumps(actions, default=str)[:10000],
    }
    clean_item = {k: v for k, v in item.items() if v is not None}
    table.put_item(Item=clean_item)
    table.put_item(
        Item={
            "PK": "AUTONOMY#RUN",
            "SK": now,
            **{
                k: v
                for k, v in clean_item.items()
                if k not in {"PK", "SK"}
            },
        }
    )
    return {
        **item,
        "actions": actions,
        "gates": {
            "min_training_samples": MIN_TRAINING_SAMPLES,
            "min_live_audit": MIN_LIVE_AUDIT,
            "min_live_accuracy": MIN_LIVE_ACCURACY,
            "max_live_brier": MAX_LIVE_BRIER,
        },
    }


def status() -> Dict[str, Any]:
    state = _previous_state()
    if not state:
        return {
            "service": "tennis-autonomy",
            "authority": "SHADOW",
            "reason": "no_autonomous_cycle_completed",
        }
    return {
        "service": "tennis-autonomy",
        **{k: v for k, v in state.items() if k not in {"PK", "SK", "actions"}},
    }


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    method = str(event.get("httpMethod", "")).upper()
    path = str(event.get("path", ""))
    result = status() if method == "GET" and path.endswith("/status") else run_cycle()
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
        },
        "body": json.dumps(result, default=str),
    }
