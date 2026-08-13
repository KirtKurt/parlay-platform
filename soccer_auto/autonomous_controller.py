"""Fail-closed orchestration and health authority for soccer_auto."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Any, Mapping

import boto3
from boto3.dynamodb.conditions import Attr, Key

from .canonical import iso_utc
from .llm_analyst import latest_validated_analysis
from .odds_api import provider_safety_config
from .storage import SoccerStore, ddb_safe, now_utc, plain


MAX_CONSECUTIVE_FAILURES = int(os.getenv("SOCCER_AUTO_MAX_CONSECUTIVE_FAILURES", "3"))

# These windows intentionally allow multiple missed schedule periods and normal
# CloudWatch metric publication delay. A component is unhealthy when it has no
# invocation in its window or when any invocation raised a Lambda error.
COMPONENT_LIVENESS: Mapping[str, tuple[str, int]] = {
    "inventory": ("SOCCER_AUTO_INVENTORY_FUNCTION", 45),
    "dispatch": ("SOCCER_AUTO_DISPATCH_FUNCTION", 10),
    "freeze": ("SOCCER_AUTO_FREEZE_FUNCTION", 10),
    "settlement": ("SOCCER_AUTO_SETTLEMENT_FUNCTION", 20),
    "trainer": ("SOCCER_AUTO_TRAINER_FUNCTION", 780),
    "llm_analyst": ("SOCCER_AUTO_LLM_ANALYST_FUNCTION", 420),
}


def _bounded_count(table: Any, limit: int = 1000) -> int:
    """Bound health-read cost; values at the cap are documented lower bounds."""
    response = table.scan(Select="COUNT", Limit=limit)
    return int(response.get("Count") or 0)


def _latest_quota(store: SoccerStore) -> dict[str, Any] | None:
    response = store.ops.query(
        KeyConditionExpression=Key("PK").eq("QUOTA"),
        ScanIndexForward=False,
        Limit=1,
        ConsistentRead=True,
    )
    rows = response.get("Items", [])
    return plain(rows[0]) if rows else None


def _queue_health(store: SoccerStore) -> dict[str, int]:
    queue_urls = {
        "collection": store.collection_queue_url,
        "dead_letter": os.environ.get("SOCCER_AUTO_COLLECTION_DLQ_URL", ""),
    }
    result = {}
    for name, url in queue_urls.items():
        if not url:
            result[name] = 0
            continue
        response = store.sqs.get_queue_attributes(
            QueueUrl=url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            ],
        )
        attributes = response.get("Attributes") or {}
        result[name] = sum(
            int(attributes.get(attribute) or 0)
            for attribute in (
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            )
        )
    return result


def _model_state(store: SoccerStore) -> dict[str, Any]:
    rows = store.model_items()
    champion = next((row for row in rows if row.get("SK") == "CHAMPION"), None)
    challengers = [row for row in rows if row.get("authority_state") == "PROSPECTIVE_SHADOW"]
    return {
        "champion_digest": champion.get("model_digest") if champion else None,
        "automatic_prediction_allowed": bool(champion and champion.get("automatic_prediction_allowed")),
        "prospective_challengers": len(challengers),
        "challenger_digests": [row.get("model_digest") for row in challengers],
    }


def _llm_state(store: SoccerStore, observed: datetime) -> dict[str, Any]:
    row = latest_validated_analysis(store, observed)
    if row is None:
        return {
            "configured": bool(os.getenv("SOCCER_AUTO_LLM_MODEL_ID")),
            "analyses": 0,
            "fresh": False,
        }
    return {
        "configured": bool(os.getenv("SOCCER_AUTO_LLM_MODEL_ID")),
        "analyses": 1,
        "fresh": True,
        "analysis_digest": row.get("analysis_digest"),
        "validated_trials": len(row.get("recommended_trials") or []),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
    }


def _metric_sum(
    cloudwatch: Any,
    *,
    function_name: str,
    metric_name: str,
    observed: datetime,
    lookback_minutes: int,
) -> tuple[float, str | None]:
    duration_seconds = lookback_minutes * 60
    # GetMetricStatistics returns at most 1,440 points. Keep the period a
    # multiple of 60 seconds while preserving enough resolution for every
    # configured liveness window.
    period = max(60, int(math.ceil(duration_seconds / 1440.0 / 60.0) * 60))
    response = cloudwatch.get_metric_statistics(
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
    timestamps = [point.get("Timestamp") for point in points if point.get("Timestamp")]
    latest = max(timestamps).isoformat() if timestamps else None
    return total, latest


def component_liveness(cloudwatch: Any, observed: datetime) -> dict[str, dict[str, Any]]:
    """Read independent Lambda heartbeats without invoking scheduled work."""
    result: dict[str, dict[str, Any]] = {}
    for component, (environment_key, lookback_minutes) in COMPONENT_LIVENESS.items():
        function_name = os.getenv(environment_key, "").strip()
        if not function_name:
            result[component] = {
                "healthy": False,
                "reason": "FUNCTION_NAME_NOT_CONFIGURED",
                "lookback_minutes": lookback_minutes,
            }
            continue
        try:
            invocations, latest = _metric_sum(
                cloudwatch,
                function_name=function_name,
                metric_name="Invocations",
                observed=observed,
                lookback_minutes=lookback_minutes,
            )
            errors, _ = _metric_sum(
                cloudwatch,
                function_name=function_name,
                metric_name="Errors",
                observed=observed,
                lookback_minutes=lookback_minutes,
            )
            if invocations < 1:
                reason = "NO_RECENT_INVOCATION"
            elif errors > 0:
                reason = "RECENT_LAMBDA_ERRORS"
            else:
                reason = "HEALTHY"
            result[component] = {
                "healthy": reason == "HEALTHY",
                "reason": reason,
                "function_name": function_name,
                "lookback_minutes": lookback_minutes,
                "invocations": int(invocations),
                "errors": int(errors),
                "latest_metric_bucket_at": latest,
            }
        except Exception as exc:
            result[component] = {
                "healthy": False,
                "reason": "METRIC_READ_FAILED",
                "function_name": function_name,
                "lookback_minutes": lookback_minutes,
                "error": str(exc)[:500],
            }
    return result


def authority_state(
    *,
    model: Mapping[str, Any],
    counts: Mapping[str, int],
    consecutive_failures: int,
    liveness_failed: bool,
    validated_llm_missing: bool,
) -> tuple[str, str]:
    if liveness_failed:
        return "DEGRADED", "SCHEDULED_COMPONENT_LIVENESS_FAILED"
    if validated_llm_missing:
        return "DEGRADED", "FRESH_VALIDATED_LLM_ANALYSIS_MISSING"
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return "DEGRADED", "FAILURE_CIRCUIT_BREAKER"
    if model.get("automatic_prediction_allowed"):
        return "AUTHORITATIVE", "CHAMPION_PROMOTED_BY_PROSPECTIVE_GATES"
    if counts.get("settlements"):
        return "SHADOW_LEARNING", "ACCUMULATING_OR_EVALUATING_TRAINING_EVIDENCE"
    return "COLLECTING", "AWAITING_IMMUTABLE_SETTLED_LABELS"


def run_cycle() -> dict[str, Any]:
    store = SoccerStore()
    observed = now_utc()
    previous = store.ops.get_item(Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True).get("Item") or {}
    actions: dict[str, Any] = {}
    failures: list[str] = []

    actions["catalog"] = {
        "scheduled": True,
        "invoked_by_controller": False,
        "registry_empty": not bool(store.list_competitions()),
    }

    # Collection, locking, settlement, training, and LLM analysis have isolated
    # EventBridge schedules.  The controller observes them rather than doubling
    # provider calls or training work every fifteen minutes.
    liveness = component_liveness(boto3.client("cloudwatch"), observed)
    for action in ("inventory", "dispatch", "freeze", "settlement", "trainer", "llm_analyst"):
        actions[action] = {
            "scheduled": True,
            "invoked_by_controller": False,
            "liveness": liveness[action],
        }
        if not liveness[action]["healthy"]:
            failures.append(f"component_liveness:{action}:{liveness[action]['reason']}")

    queues = _queue_health(store)
    if queues.get("dead_letter", 0) > 0:
        failures.append("collection_dead_letter_queue_not_empty")
    settlement_conflicts = sum(
        1
        for _ in SoccerStore.scan_all(
            store.ops,
            FilterExpression=Attr("PK").eq("SETTLEMENT_CONFLICT"),
        )
    )
    if settlement_conflicts:
        failures.append("immutable_settlement_conflicts_present")

    llm = _llm_state(store, observed)
    validated_llm_missing = bool(llm["configured"] and not llm["fresh"])
    if validated_llm_missing:
        failures.append("llm_analyst:fresh_validated_analysis_missing")

    consecutive_failures = int(previous.get("consecutive_failures") or 0) + 1 if failures else 0
    counts = {
        "competitions": len(store.list_competitions()),
        "events": _bounded_count(store.events),
        "snapshot_slots": _bounded_count(store.slots),
        "locks": _bounded_count(store.locks),
        "settlements": _bounded_count(store.settlements),
        "predictions": _bounded_count(store.predictions),
        "models": _bounded_count(store.models),
    }
    model = _model_state(store)
    liveness_failed = any(not row["healthy"] for row in liveness.values())
    authority, reason = authority_state(
        model=model,
        counts=counts,
        consecutive_failures=consecutive_failures,
        liveness_failed=liveness_failed,
        validated_llm_missing=validated_llm_missing,
    )
    observed_at = iso_utc(observed)
    state = {
        "PK": "AUTONOMY",
        "SK": "STATE",
        "entity_type": "SOCCER_AUTONOMY_STATE",
        "ok": True,
        "system": "soccer_auto",
        "authority": authority,
        "reason": reason,
        "promotion_blocked": bool(failures),
        "automatic_prediction_allowed": model["automatic_prediction_allowed"] and not failures,
        "consecutive_failures": consecutive_failures,
        "failures": failures,
        "actions": actions,
        "component_liveness": liveness,
        "component_liveness_complete": not liveness_failed,
        "queues": queues,
        "settlement_conflicts": settlement_conflicts,
        "counts": counts,
        "counts_are_lower_bounds_at": 1000,
        "model": model,
        "llm_analyst": llm,
        "latest_quota": _latest_quota(store),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": store.provider_429_status(observed_at=observed),
        "updated_at": observed_at,
    }
    store.ops.put_item(Item=ddb_safe(state))

    return plain(state)


def controller_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    return run_cycle()
