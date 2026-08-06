#!/usr/bin/env python3
"""Reconcile the bounded MLB prospective lifecycle backlog before training.

This deployment-only repair uses the existing protected lock and settlement
Lambdas. It never writes DynamoDB directly, never creates a prediction after a
game starts, and never changes promotion, champion, wagering, or production
model authority.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config


VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v1-protected-lambda-replay"
ET = ZoneInfo("America/New_York")
LOCK_LOGICAL_ID = "MLBDailyPickLockFunction"
RESULTS_LOGICAL_ID = "MLBResultsSchedulerFunction"
TRAINER_LOGICAL_ID = "MLBMLTrainingFunction"
RELEASE_CUTOFF_ENV = "MLB_ML_RELEASE_CUTOFF_UTC"
DEFAULT_MAX_SLATE_DAYS = 14


class ReconciliationError(RuntimeError):
    """Fail-closed prospective backlog reconciliation error."""


@dataclass(frozen=True)
class StackFunctions:
    lock: str
    results: str
    trainer: str


def _json_object(value: Any, *, error: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise ReconciliationError(error) from exc
    if not isinstance(parsed, dict):
        raise ReconciliationError(error)
    return parsed


def _integer(value: Any, *, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"{field}_invalid") from exc
    if parsed < 0:
        raise ReconciliationError(f"{field}_invalid")
    return parsed


def _utc(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ReconciliationError(f"{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def prospective_slate_dates(
    release_cutoff_utc: str,
    *,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = DEFAULT_MAX_SLATE_DAYS,
) -> List[str]:
    """Return cutoff-date through yesterday ET, with a hard bounded horizon."""

    if max_slate_days < 1:
        raise ReconciliationError("max_slate_days_invalid")
    cutoff_date = _utc(release_cutoff_utc, field="release_cutoff").astimezone(ET).date()
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    yesterday_et = now.astimezone(ET).date() - timedelta(days=1)
    if cutoff_date > yesterday_et:
        return []
    day_count = (yesterday_et - cutoff_date).days + 1
    if day_count > max_slate_days:
        raise ReconciliationError("prospective_backlog_exceeds_bounded_horizon")
    return [
        (cutoff_date + timedelta(days=offset)).isoformat()
        for offset in range(day_count)
    ]


def _resource_id(cloudformation: Any, stack_name: str, logical_id: str) -> str:
    response = cloudformation.describe_stack_resource(
        StackName=stack_name,
        LogicalResourceId=logical_id,
    )
    physical_id = str(
        ((response.get("StackResourceDetail") or {}).get("PhysicalResourceId"))
        or ""
    ).strip()
    if not physical_id:
        raise ReconciliationError(f"{logical_id}_physical_id_missing")
    return physical_id


def resolve_stack_functions(cloudformation: Any, stack_name: str) -> StackFunctions:
    return StackFunctions(
        lock=_resource_id(cloudformation, stack_name, LOCK_LOGICAL_ID),
        results=_resource_id(cloudformation, stack_name, RESULTS_LOGICAL_ID),
        trainer=_resource_id(cloudformation, stack_name, TRAINER_LOGICAL_ID),
    )


def release_cutoff(lambda_client: Any, trainer_function: str) -> str:
    response = lambda_client.get_function_configuration(FunctionName=trainer_function)
    variables = ((response.get("Environment") or {}).get("Variables") or {})
    value = str(variables.get(RELEASE_CUTOFF_ENV) or "").strip()
    _utc(value, field="release_cutoff")
    return value


def _read_lambda_payload(response: Mapping[str, Any]) -> bytes:
    stream = response.get("Payload")
    if stream is None or not hasattr(stream, "read"):
        raise ReconciliationError("lambda_payload_stream_missing")
    close = getattr(stream, "close", None)
    if not callable(close):
        raise ReconciliationError("lambda_payload_stream_not_closeable")
    try:
        body = stream.read()
    finally:
        close()
    if not isinstance(body, bytes):
        raise ReconciliationError("lambda_payload_not_bytes")
    return body


def invoke_json(lambda_client: Any, function_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    status_code = _integer(response.get("StatusCode"), field="lambda_status_code")
    payload_bytes = _read_lambda_payload(response)
    if status_code != 200:
        raise ReconciliationError("lambda_invoke_status_not_200")
    if response.get("FunctionError"):
        raise ReconciliationError("lambda_function_error")
    payload = _json_object(
        payload_bytes.decode("utf-8"),
        error="lambda_response_json_invalid",
    )
    if "statusCode" not in payload:
        return payload
    application_status = _integer(
        payload.get("statusCode"), field="application_status_code"
    )
    body = payload.get("body")
    application = (
        _json_object(body, error="application_body_json_invalid")
        if isinstance(body, str)
        else dict(body or {})
    )
    if application_status < 200 or application_status >= 300:
        raise ReconciliationError("lambda_application_status_not_success")
    return application


def _official_schedule_proven(payload: Mapping[str, Any], progress: Mapping[str, Any]) -> bool:
    authority = str(
        payload.get("officialScheduleAuthorityVersion")
        or progress.get("officialScheduleAuthorityVersion")
        or ""
    )
    source = str(
        payload.get("rosterAuthorityMode")
        or progress.get("rosterAuthorityMode")
        or ""
    )
    backed = payload.get("officialScheduleBacked")
    if backed is None:
        backed = progress.get("officialScheduleBacked")
    return bool(
        backed is True
        or authority.startswith("MLB-OFFICIAL-SCHEDULE-AUTHORITY-")
        or source == "MLB_STATS_API_EXACT_DATE"
    )


def validate_lock_result(payload: Mapping[str, Any], slate_date: str) -> Dict[str, Any]:
    if payload.get("ok") is not True or payload.get("sport") != "mlb":
        raise ReconciliationError("lock_reconciliation_unhealthy")
    if str(payload.get("slateDateEt") or "") != slate_date:
        raise ReconciliationError("lock_reconciliation_slate_mismatch")
    progress = payload.get("perGameLockProgress") or {}
    if not isinstance(progress, Mapping):
        raise ReconciliationError("lock_progress_missing")

    games = progress.get("games") or []
    if not isinstance(games, list):
        raise ReconciliationError("lock_progress_games_invalid")
    manifest_count = _integer(
        progress.get("manifestGameCount", len(games)),
        field="manifest_game_count",
    )
    if manifest_count != len(games) and games:
        raise ReconciliationError("lock_progress_manifest_count_mismatch")
    if not _official_schedule_proven(payload, progress):
        raise ReconciliationError("official_schedule_authority_unproven")

    if manifest_count == 0:
        official_count = payload.get("officialScheduleGameCount")
        if official_count is None:
            official_count = progress.get("officialScheduleGameCount")
        if official_count is None or _integer(
            official_count, field="official_schedule_game_count"
        ) != 0:
            raise ReconciliationError("official_zero_game_slate_unproven")
        return {
            "slateDateEt": slate_date,
            "manifestGameCount": 0,
            "canonicalPredictionCount": 0,
            "terminalNoPredictionCount": 0,
            "lockOutcomeCount": 0,
            "offDay": True,
        }

    canonical_count = _integer(
        progress.get("canonicalCount"), field="canonical_count"
    )
    terminal_count = _integer(
        progress.get("noPredictionDataCount", progress.get("terminalNoPredictionCount", 0)),
        field="terminal_no_prediction_count",
    )
    lock_outcome_count = _integer(
        progress.get("lockOutcomeCount"), field="lock_outcome_count"
    )
    missed_count = _integer(progress.get("missedCount"), field="missed_count")
    due_count = _integer(progress.get("dueMissingCount"), field="due_missing_count")
    if missed_count or due_count:
        raise ReconciliationError("prospective_slate_still_unresolved")
    if lock_outcome_count != manifest_count:
        raise ReconciliationError("prospective_slate_terminal_coverage_incomplete")
    if canonical_count + terminal_count != lock_outcome_count:
        raise ReconciliationError("prospective_slate_terminal_counts_inconsistent")
    if payload.get("lockStatusComplete") is not True:
        raise ReconciliationError("prospective_slate_lock_status_incomplete")
    return {
        "slateDateEt": slate_date,
        "manifestGameCount": manifest_count,
        "canonicalPredictionCount": canonical_count,
        "terminalNoPredictionCount": terminal_count,
        "lockOutcomeCount": lock_outcome_count,
        "offDay": False,
    }


def validate_settlement_result(payload: Mapping[str, Any], slate_date: str) -> Dict[str, Any]:
    if payload.get("ok") is not True:
        raise ReconciliationError("prospective_settlement_unhealthy")
    returned_date = str(
        payload.get("slateDateEt")
        or payload.get("slate_date")
        or payload.get("date")
        or ""
    )
    if returned_date and returned_date != slate_date:
        raise ReconciliationError("prospective_settlement_slate_mismatch")
    return {
        "slateDateEt": slate_date,
        "ok": True,
        "finalized": payload.get("slateFinalized"),
        "settledLabelCount": payload.get("settledLabelCount", payload.get("labelCount")),
    }


def reconcile(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    now_utc: Optional[datetime] = None,
    max_slate_days: int = DEFAULT_MAX_SLATE_DAYS,
) -> Dict[str, Any]:
    functions = resolve_stack_functions(cloudformation, stack_name)
    cutoff = release_cutoff(lambda_client, functions.trainer)
    slate_dates = prospective_slate_dates(
        cutoff,
        now_utc=now_utc,
        max_slate_days=max_slate_days,
    )
    rows: List[Dict[str, Any]] = []
    for slate_date in slate_dates:
        lock_event = {
            "sport": "mlb",
            "run": "prospective_terminal_backlog_reconciliation",
            "slateDateEt": slate_date,
            "force": True,
        }
        lock_payload = invoke_json(lambda_client, functions.lock, lock_event)
        lock_evidence = validate_lock_result(lock_payload, slate_date)

        settlement_event = {
            "sport": "mlb",
            "run": "prospective_backlog_settlement",
            "slate_date": slate_date,
            "days_from": 0,
        }
        settlement_payload = invoke_json(
            lambda_client,
            functions.results,
            settlement_event,
        )
        settlement_evidence = validate_settlement_result(
            settlement_payload,
            slate_date,
        )
        rows.append(
            {
                **lock_evidence,
                "settlement": settlement_evidence,
                "protectedLockReplay": True,
                "directTableWrite": False,
                "postStartPredictionCreationAllowed": False,
            }
        )

    return {
        "ok": True,
        "version": VERSION,
        "stackName": stack_name,
        "releaseCutoffUtc": cutoff,
        "firstSlateDateEt": slate_dates[0] if slate_dates else None,
        "lastSlateDateEt": slate_dates[-1] if slate_dates else None,
        "reconciledSlateCount": len(rows),
        "slates": rows,
        "boundedMaximumSlateDays": max_slate_days,
        "protectedLockReplay": True,
        "protectedSettlementReplay": True,
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "promotionAuthorityChanged": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(report), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-slate-days",
        type=int,
        default=DEFAULT_MAX_SLATE_DAYS,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    session = boto3.session.Session(region_name=args.region)
    config = Config(retries={"max_attempts": 3, "mode": "standard"})
    cloudformation = session.client("cloudformation", config=config)
    lambda_client = session.client("lambda", config=config)
    try:
        report = reconcile(
            cloudformation,
            lambda_client,
            stack_name=args.stack_name,
            max_slate_days=args.max_slate_days,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "stackName": args.stack_name,
            "error": f"{type(exc).__name__}:{exc}",
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChanged": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
        _write_report(args.output, report)
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    _write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
