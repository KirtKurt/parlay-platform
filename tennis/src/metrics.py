from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable


def _sum(rows: Iterable[Dict[str, Any]], key: str) -> float:
    total = 0.0
    for row in rows:
        try:
            total += float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _minimum_quota_remaining(meta: Dict[str, Any]) -> float:
    values = []
    quota = meta.get("quotaStatus") or {}
    for value in (quota.get("requestsRemaining"), quota.get("remainingAfterAttempt")):
        try:
            if value is not None:
                values.append(float(value))
        except (TypeError, ValueError):
            pass
    for row in meta.get("usage") or []:
        try:
            value = row.get("requestsRemaining")
            if value is not None:
                values.append(float(value))
        except (AttributeError, TypeError, ValueError):
            continue
    return min(values) if values else -1.0


def _freshness_seconds(report: Dict[str, Any]) -> float:
    try:
        slot = datetime.fromisoformat(str(report["slot_utc"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(
            str(report["observed_at_utc"]).replace("Z", "+00:00")
        )
        if slot.tzinfo is None:
            slot = slot.replace(tzinfo=timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max((observed - slot).total_seconds(), 0.0)
    except (KeyError, TypeError, ValueError):
        return 0.0


def _covered_snapshot_count(runs: Iterable[Dict[str, Any]]) -> float:
    total = 0.0
    for row in runs:
        if "covered_event_count" in row:
            try:
                total += float(row.get("covered_event_count") or 0)
            except (TypeError, ValueError):
                continue
        else:
            total += _sum((row,), "snapshot_created_count")
            total += _sum((row,), "snapshot_deduped_count")
    return total


def emit_report_metrics(
    report: Dict[str, Any], *, duration_ms: float, namespace: str
) -> Dict[str, float]:
    runs = [row for row in (report.get("slate_runs") or []) if isinstance(row, dict)]
    expected = _sum(runs, "event_count")
    stored = _covered_snapshot_count(runs)
    coverage = (100.0 * stored / expected) if expected else 100.0
    meta = report.get("odds_meta") or {}
    rejection_counts = meta.get("bookRejectionReasonCounts") or {}
    quota = meta.get("quotaStatus") or {}
    values = {
        "Invocation": 1.0,
        "DurationMs": max(float(duration_ms), 0.0),
        "ActiveSlates": float(len(runs)),
        "CompleteSlates": float(sum(1 for row in runs if row.get("complete") is True)),
        "RetryRequired": 1.0 if report.get("retry_required") else 0.0,
        "ExpectedEvents": expected,
        "StoredSnapshots": stored,
        "CoveragePercent": coverage,
        "CollectorFreshnessSeconds": _freshness_seconds(report),
        "EventsWithoutOdds": _sum(runs, "events_without_odds_count"),
        "PostStartExclusions": _sum(runs, "events_started_during_fetch_count"),
        "ArchiveFailures": _sum(runs, "archive_failure_count"),
        "PaidOddsCalls": float(report.get("odds_endpoint_calls") or 0),
        "QuotaRemaining": _minimum_quota_remaining(meta),
        "ProjectedDailyRequestCost": float(quota.get("projectedDailyRequestCost") or 0),
        "QuotaDailyBudgetExceeded": 1.0 if quota.get("dailyBudgetExceeded") else 0.0,
        "RejectedBooks": float(sum(int(value) for value in rejection_counts.values())),
        "StaleBookRejections": float(rejection_counts.get("stale_last_update") or 0),
    }
    metric_defs = [
        {"Name": name, "Unit": "Milliseconds" if name == "DurationMs" else "None"}
        for name in values
    ]
    envelope: Dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [["Sport", "Mode"]],
                    "Metrics": metric_defs,
                }
            ],
        },
        "Sport": "tennis",
        "Mode": str(report.get("mode") or "RULE_BASED_SHADOW"),
        "RunStatus": str(report.get("run_status") or "UNKNOWN"),
        **values,
    }
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return values


def emit_failure_metrics(
    *,
    error_code: str,
    failure_attempt_count: int,
    retry_exhausted: bool,
    duration_ms: float,
    namespace: str,
) -> Dict[str, float]:
    """Emit failure evidence even when the collector cannot produce a report."""

    values = {
        "CollectorFailure": 1.0,
        "FailureAttemptCount": float(max(int(failure_attempt_count), 0)),
        "RetryExhausted": 1.0 if retry_exhausted else 0.0,
        "FailureDurationMs": max(float(duration_ms), 0.0),
    }
    envelope: Dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [["Sport", "Mode"]],
                    "Metrics": [
                        {
                            "Name": name,
                            "Unit": (
                                "Milliseconds"
                                if name == "FailureDurationMs"
                                else "None"
                            ),
                        }
                        for name in values
                    ],
                }
            ],
        },
        "Sport": "tennis",
        "Mode": "RULE_BASED_SHADOW",
        "FailureCode": error_code,
        **values,
    }
    print(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
    return values
