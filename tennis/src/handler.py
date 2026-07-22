from __future__ import annotations

import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict

from config import TennisConfig
from credentials import resolve_secret
from archive import S3ParquetTennisArchive
from contracts import floor_to_slot, utc_iso
from metrics import emit_failure_metrics, emit_report_metrics
from pipeline import TennisPipeline
from provider import OddsApiTennisProvider
from storage import DynamoTennisStore


_pipeline = None
_MAX_INVOCATION_ATTEMPTS = 3


def _safe_failure_code(exc: Exception) -> str:
    message = str(exc).strip()
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", message):
        return message
    return f"unexpected_{type(exc).__name__.lower()}"


def _journal_store():
    store = getattr(_pipeline, "store", None)
    if store is not None:
        return store
    snapshots_table = os.environ.get("TENNIS_SNAPSHOTS_TABLE", "").strip()
    signals_table = os.environ.get("TENNIS_SIGNALS_TABLE", "").strip()
    if not snapshots_table or not signals_table:
        return None
    return DynamoTennisStore(snapshots_table, signals_table)


def _structured_log(record_type: str, **fields: Any) -> None:
    print(
        json.dumps(
            {"record_type": record_type, "sport": "tennis", **fields},
            sort_keys=True,
            default=str,
        )
    )


def _build_pipeline() -> TennisPipeline:
    config = TennisConfig.from_env()
    if not config.odds_api_key:
        config = replace(
            config,
            odds_api_key=resolve_secret(
                os.environ.get("TENNIS_ODDS_API_SECRET_ARN", "")
            ),
        )
    config.validate_runtime()
    provider = OddsApiTennisProvider(config)
    store = DynamoTennisStore(config.snapshots_table, config.signals_table)
    archive = S3ParquetTennisArchive(config.archive_bucket)
    return TennisPipeline(config, provider, store, archive)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    global _pipeline
    event = event or {}
    requested_sport = str(event.get("sport") or "tennis").strip().lower()
    if requested_sport != "tennis":
        raise RuntimeError("tennis_scheduler_rejects_non_tennis_event")
    mode = str(event.get("mode") or "scheduled").strip().lower()
    if mode == "canary_health":
        return {
            "ok": True,
            "sport": "tennis",
            "mode": "canary_health",
            "model_state": os.environ.get("TENNIS_MODEL_STATE", "RULE_BASED_SHADOW"),
            "schedule_invoked": False,
            "network_calls": 0,
            "credentials_present": {
                "dedicated_odds_secret": bool(
                    os.environ.get("TENNIS_ODDS_API_SECRET_ARN", "").strip()
                ),
            },
        }
    if mode != "scheduled":
        raise RuntimeError("tennis_scheduler_only_accepts_scheduled_mode")
    if not event.get("time"):
        raise RuntimeError("eventbridge_scheduled_time_required")
    scheduled_at = None
    if event.get("time"):
        try:
            scheduled_at = datetime.fromisoformat(
                str(event["time"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("invalid_eventbridge_scheduled_time") from exc
    if scheduled_at.tzinfo is None:
        raise RuntimeError("invalid_eventbridge_scheduled_time")
    scheduled_at = scheduled_at.astimezone(timezone.utc)
    slot = floor_to_slot(scheduled_at, 15)
    slot_utc = utc_iso(slot)
    scheduled_at_utc = utc_iso(scheduled_at)
    delivery_id = str(event.get("id") or f"slot-{slot_utc}")
    lease_owner = str(
        getattr(context, "aws_request_id", "") or f"local-{time.time_ns()}"
    )
    started = time.monotonic()
    store = None
    lease_acquired = False
    failure = None
    report = None
    try:
        if _pipeline is None:
            _pipeline = _build_pipeline()
        store = _pipeline.store
        slot = floor_to_slot(
            scheduled_at,
            _pipeline.config.pull_interval_minutes,
        )
        slot_utc = utc_iso(slot)
        acquired_at = datetime.now(timezone.utc)
        lease_acquired = store.acquire_slot_lease(
            slot_utc,
            owner_id=lease_owner,
            acquired_at_utc=utc_iso(acquired_at),
            now_epoch=int(acquired_at.timestamp()),
            lease_seconds=_pipeline.config.slot_lease_seconds,
        )
        if not lease_acquired:
            raise RuntimeError("tennis_slot_lease_busy")
        report = _pipeline.run(slot_anchor_utc=scheduled_at)
        print(json.dumps(report, sort_keys=True, default=str))
        namespace = os.environ.get("TENNIS_METRICS_NAMESPACE", "Inqsi/TennisCollector")
        emit_report_metrics(
            report,
            duration_ms=(time.monotonic() - started) * 1000.0,
            namespace=namespace,
        )
        if report.get("retry_required"):
            raise RuntimeError("tennis_slot_retry_required")
    except Exception as exc:
        failure = exc
    finally:
        if lease_acquired:
            try:
                if not store.release_slot_lease(slot_utc, owner_id=lease_owner):
                    raise RuntimeError("tennis_slot_lease_release_failed")
            except Exception as release_exc:
                if failure is None:
                    failure = release_exc
                else:
                    _structured_log(
                        "tennis_slot_lease_release_suppressed",
                        slot_utc=slot_utc,
                        delivery_id=delivery_id,
                        request_id=lease_owner,
                        error_code=_safe_failure_code(release_exc),
                    )

    namespace = os.environ.get("TENNIS_METRICS_NAMESPACE", "Inqsi/TennisCollector")
    if failure is not None:
        error_code = _safe_failure_code(failure)
        failed_at_utc = utc_iso(datetime.now(timezone.utc))
        journal = None
        try:
            store = store or _journal_store()
            if store is not None:
                journal = store.record_invocation_failure(
                    slot_utc,
                    delivery_id=delivery_id,
                    scheduled_at_utc=scheduled_at_utc,
                    failed_at_utc=failed_at_utc,
                    request_id=lease_owner,
                    error_code=error_code,
                    max_attempts=_MAX_INVOCATION_ATTEMPTS,
                )
        except Exception as journal_exc:
            _structured_log(
                "tennis_failure_journal_write_failed",
                slot_utc=slot_utc,
                delivery_id=delivery_id,
                request_id=lease_owner,
                original_error_code=error_code,
                journal_error_code=_safe_failure_code(journal_exc),
            )
        attempt_count = int((journal or {}).get("failure_attempt_count") or 0)
        retry_exhausted = bool((journal or {}).get("retry_exhausted"))
        try:
            emit_failure_metrics(
                error_code=error_code,
                failure_attempt_count=attempt_count,
                retry_exhausted=retry_exhausted,
                duration_ms=(time.monotonic() - started) * 1000.0,
                namespace=namespace,
            )
        except Exception as metric_exc:
            _structured_log(
                "tennis_failure_metric_emit_failed",
                slot_utc=slot_utc,
                delivery_id=delivery_id,
                request_id=lease_owner,
                original_error_code=error_code,
                metric_error_code=_safe_failure_code(metric_exc),
            )
        _structured_log(
            "tennis_collector_failure",
            slot_utc=slot_utc,
            scheduled_at_utc=scheduled_at_utc,
            delivery_id=delivery_id,
            request_id=lease_owner,
            error_code=error_code,
            failure_attempt_count=attempt_count,
            retry_exhausted=retry_exhausted,
        )
        raise failure.with_traceback(failure.__traceback__)

    try:
        recovered = store.resolve_invocation_failure(
            slot_utc,
            delivery_id=delivery_id,
            recovered_at_utc=utc_iso(datetime.now(timezone.utc)),
            request_id=lease_owner,
        )
        if recovered is not None:
            _structured_log(
                "tennis_collector_retry_recovered",
                slot_utc=slot_utc,
                delivery_id=delivery_id,
                request_id=lease_owner,
                failure_attempt_count=int(recovered.get("failure_attempt_count") or 0),
            )
    except Exception as journal_exc:
        # A recovery-marker failure must never trigger another paid odds pull.
        _structured_log(
            "tennis_failure_journal_recovery_failed",
            slot_utc=slot_utc,
            delivery_id=delivery_id,
            request_id=lease_owner,
            journal_error_code=_safe_failure_code(journal_exc),
        )
    return report
