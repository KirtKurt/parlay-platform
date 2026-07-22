from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Dict

from config import TennisConfig
from credentials import resolve_secret
from archive import S3ParquetTennisArchive
from metrics import emit_report_metrics
from pipeline import TennisPipeline
from provider import OddsApiTennisProvider
from storage import DynamoTennisStore


_pipeline = None


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
    if _pipeline is None:
        _pipeline = _build_pipeline()
    scheduled_at = None
    if event.get("time"):
        try:
            scheduled_at = datetime.fromisoformat(
                str(event["time"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError("invalid_eventbridge_scheduled_time") from exc
    started = time.monotonic()
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
    return report
