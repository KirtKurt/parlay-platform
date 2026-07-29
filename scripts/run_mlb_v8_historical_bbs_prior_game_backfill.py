#!/usr/bin/env python3
"""Backfill MLB V8 with leakage-safe historical BBD prior-game features.

Unlike target-game fundamentals, this path needs no reconstructed injury or lineup
snapshot. It computes form, run production, streak, and rest from BigBallsData games
completed on strictly earlier slate dates. Target outcomes and same-day results are
never used. Eligible snapshots are published through the existing immutable V8 BBS
manifest pointer and remain shadow-only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import boto3

from bigballsdata_client import BigBallsDataClient
import mlb_v8_historical_bbs_overlay_v1 as overlay
import mlb_v8_historical_bbs_prior_game_v1 as prior_game
import run_mlb_v8_historical_bbs_backfill as core

VERSION = "MLB-V8-HISTORICAL-BBS-PRIOR-GAME-BACKFILL-v1"
REPORT_TYPE = "MLB_V8_HISTORICAL_BBS_PRIOR_GAME_BACKFILL"
DEFAULT_COVERAGE_START = "2026-03-01"
DEFAULT_HISTORY_DAYS = 45
DEFAULT_BATCH_SIZE = 100


def _date_range(start: date, end: date) -> List[str]:
    values: List[str] = []
    cursor = start
    while cursor <= end:
        values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


def _identity(row: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        str(row.get("officialGamePk") or ""),
        str(row.get("predictionLockAtUtc") or ""),
    )


def _historical_bucket(outputs: Mapping[str, Any]) -> str:
    return str(outputs.get("HistoricalArtifactsBucketName") or "").strip()


def _load_previous_compatible(
    table: Any,
    s3: Any,
    current_identity: Mapping[Tuple[str, str], Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    previous, revision = core._load_previous_manifest(table, s3)
    records: List[Dict[str, Any]] = []
    for raw in (previous or {}).get("records") or []:
        if not isinstance(raw, Mapping):
            continue
        if _identity(raw) not in current_identity:
            continue
        records.append(copy.deepcopy(dict(raw)))
    return records, revision


def _history_material(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    material: List[Dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        game = prior_game.completed_game(raw)
        if game is None:
            continue
        material.append(
            {
                "providerId": game.provider_id,
                "gameDay": game.game_day,
                "kickoffUtc": game.kickoff_utc,
                "homeTeam": game.home_team,
                "awayTeam": game.away_team,
                "homeRuns": game.home_runs,
                "awayRuns": game.away_runs,
            }
        )
    return sorted(
        material,
        key=lambda value: (
            value["gameDay"],
            value["kickoffUtc"],
            value["providerId"],
        ),
    )


def _target_crosswalks(
    provider_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    canonical_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    tolerance_minutes: int,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    accepted: Dict[str, Dict[str, Any]] = {}
    quarantine: List[Dict[str, Any]] = []
    for game_day, canonical in sorted(canonical_by_day.items()):
        result = core.crosswalk_provider_rows(
            provider_by_day.get(game_day) or [],
            canonical,
            tolerance_minutes=tolerance_minutes,
        )
        accepted.update(result.get("accepted") or {})
        quarantine.extend(result.get("quarantined") or [])
    return accepted, quarantine


def _snapshot(
    canonical: Mapping[str, Any],
    derived: Mapping[str, Any],
    *,
    provider_match: Optional[Mapping[str, Any]],
    history_start: str,
    history_end: str,
    history_row_count: int,
    history_fingerprint: str,
    created_at: datetime,
) -> Dict[str, Any]:
    errors = list(derived.get("eligibilityErrors") or [])
    training_eligible = derived.get("trainingEligible") is True and not errors
    value: Dict[str, Any] = {
        "version": overlay.SNAPSHOT_VERSION,
        "authority": overlay.AUTHORITY,
        "snapshotRole": "BBD_STRICTLY_PRIOR_COMPLETED_GAME_FEATURES_AT_T_MINUS_45",
        "createdAtUtc": created_at.isoformat(),
        "officialGamePk": str(canonical.get("officialGamePk") or ""),
        "providerMatchId": (
            provider_match.get("providerMatchId") if provider_match else None
        ),
        "predictionLockAtUtc": canonical.get("predictionLockAtUtc"),
        "slateDateEt": canonical.get("slateDateEt"),
        "homeTeam": canonical.get("homeTeam"),
        "awayTeam": canonical.get("awayTeam"),
        "home": copy.deepcopy(dict(derived.get("home") or {})),
        "away": copy.deepcopy(dict(derived.get("away") or {})),
        "providerEvidence": {
            "source": "bigballsdata",
            "historyStartDate": history_start,
            "historyEndDate": history_end,
            "historyRowCount": history_row_count,
            "historyFingerprint": history_fingerprint,
            "targetProviderCrosswalkAvailable": provider_match is not None,
            "featureVersion": prior_game.VERSION,
        },
        "historyBoundary": "strictly_prior_bbd_completed_slate_dates",
        "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False,
        "priorCompletedGamesUsed": True,
        "pointInTimeVerified": training_eligible,
        "postgameFieldsExcluded": True,
        "selectionUsedOutcomes": False,
        "trainingEligible": training_eligible,
        "eligibilityErrors": sorted(set(str(value) for value in errors)),
        "productionAuthorityChanged": False,
    }
    value["fingerprint"] = overlay.snapshot_fingerprint(value)
    return value


def run(
    *,
    region: str,
    table_name: str,
    historical_stack: str,
    batch_size: int,
    history_days: int,
    coverage_start: str,
    start_tolerance_minutes: int,
    output: Path,
    client_factory: Any = BigBallsDataClient,
) -> Dict[str, Any]:
    if batch_size < 1 or batch_size > 250:
        raise ValueError("batch_size must be between 1 and 250")
    if history_days < 14 or history_days > 120:
        raise ValueError("history_days must be between 14 and 120")
    coverage_start_date = date.fromisoformat(coverage_start)
    created = datetime.now(timezone.utc)

    cf = boto3.client("cloudformation", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    table = ddb.Table(table_name)
    outputs = core._outputs(cf, historical_stack)
    bucket = _historical_bucket(outputs)
    if not outputs.get("HistoricalOptimizerFunctionName") or not bucket:
        raise RuntimeError("historical optimizer outputs are incomplete")

    state_item = table.get_item(
        Key={"PK": core.STATE_PK, "SK": core.STATE_SK},
        ConsistentRead=True,
    ).get("Item")
    if not state_item:
        raise RuntimeError("historical optimizer state is missing")
    state = core._plain(state_item.get("data") or {})
    canonical_games = core._load_canonical_games(state, s3)
    current_identity = {_identity(row): row for row in canonical_games}
    previous_records, previous_revision = _load_previous_compatible(
        table, s3, current_identity
    )
    processed = {_identity(row) for row in previous_records}

    supported = [
        row
        for row in canonical_games
        if date.fromisoformat(str(row.get("slateDateEt") or "1900-01-01"))
        >= coverage_start_date
    ]
    pending = [row for row in supported if _identity(row) not in processed]
    selected = sorted(
        pending,
        key=lambda row: (
            str(row.get("slateDateEt") or ""),
            str(row.get("predictionLockAtUtc") or ""),
            str(row.get("officialGamePk") or ""),
        ),
        reverse=True,
    )[:batch_size]

    provider_calls = 0
    provider_by_day: Dict[str, List[Mapping[str, Any]]] = {}
    queried_dates: List[str] = []
    history_rows: List[Mapping[str, Any]] = []
    history_start = None
    history_end = None
    if selected:
        selected_dates = [date.fromisoformat(str(row["slateDateEt"])) for row in selected]
        history_start_date = min(selected_dates) - timedelta(days=history_days)
        history_end_date = max(selected_dates)
        history_start = history_start_date.isoformat()
        history_end = history_end_date.isoformat()
        client = client_factory(timeout_seconds=8, max_attempts=2)
        for game_day in _date_range(history_start_date, history_end_date):
            envelope = client.list_mlb_matches(game_day, limit=200, stored=True)
            provider_calls += 1
            queried_dates.append(game_day)
            rows = [
                row
                for row in envelope.get("data") or []
                if isinstance(row, Mapping)
            ]
            provider_by_day[game_day] = rows
            history_rows.extend(rows)

    ledger = prior_game.build_team_ledger(history_rows)
    selected_by_day: Dict[str, List[Mapping[str, Any]]] = {}
    all_by_day: Dict[str, List[Mapping[str, Any]]] = {}
    for row in canonical_games:
        all_by_day.setdefault(str(row.get("slateDateEt") or ""), []).append(row)
    for row in selected:
        selected_by_day.setdefault(str(row.get("slateDateEt") or ""), []).append(row)
    target_crosswalk, quarantine = _target_crosswalks(
        provider_by_day,
        {day: all_by_day.get(day) or rows for day, rows in selected_by_day.items()},
        tolerance_minutes=start_tolerance_minutes,
    )

    history_material = _history_material(history_rows)
    history_fingerprint = core._sha(history_material)
    new_records: List[Dict[str, Any]] = []
    eligibility_error_counts: Dict[str, int] = {}
    for canonical in selected:
        derived = prior_game.derive_game_features(ledger, canonical)
        for error in derived.get("eligibilityErrors") or []:
            key = str(error)
            eligibility_error_counts[key] = eligibility_error_counts.get(key, 0) + 1
        snapshot = _snapshot(
            canonical,
            derived,
            provider_match=target_crosswalk.get(str(canonical.get("officialGamePk") or "")),
            history_start=history_start or "",
            history_end=history_end or "",
            history_row_count=len(history_material),
            history_fingerprint=history_fingerprint,
            created_at=created,
        )
        new_records.append(
            {
                **{
                    key: canonical.get(key)
                    for key in (
                        "slateDateEt",
                        "officialGamePk",
                        "predictionLockAtUtc",
                        "homeTeam",
                        "awayTeam",
                    )
                },
                "providerMatchId": snapshot.get("providerMatchId"),
                "trainingEligible": snapshot["trainingEligible"],
                "eligibilityErrors": snapshot["eligibilityErrors"],
                "snapshot": snapshot if snapshot["trainingEligible"] else None,
                "featureSource": prior_game.VERSION,
            }
        )

    records = sorted(
        previous_records + new_records,
        key=lambda row: (
            str(row.get("slateDateEt") or ""),
            str(row.get("predictionLockAtUtc") or ""),
            str(row.get("officialGamePk") or ""),
        ),
    )
    eligible = sum(row.get("trainingEligible") is True for row in records)
    new_eligible = sum(row.get("trainingEligible") is True for row in new_records)
    supported_count = len(supported)
    remaining_supported = max(0, supported_count - len(records))
    unsupported_count = max(0, len(canonical_games) - supported_count)

    manifest: Dict[str, Any] = {
        "version": overlay.MANIFEST_VERSION,
        "backfillVersion": VERSION,
        "authority": overlay.AUTHORITY,
        "createdAtUtc": created.isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "sourceHistoricalStateRevision": state.get("revision"),
        "sourceFeatureDatasetVersion": state.get("featureDatasetVersion"),
        "sourceCorpusFingerprint": core._sha(
            [
                {
                    "slateDateEt": row.get("slateDateEt"),
                    "officialGamePk": row.get("officialGamePk"),
                    "predictionLockAtUtc": row.get("predictionLockAtUtc"),
                }
                for row in canonical_games
            ]
        ),
        "selectionRule": "newest unprocessed canonical games within BBD coverage",
        "selectionUsedOutcomes": False,
        "targetGameOutcomeUsed": False,
        "sameDayResultsExcluded": True,
        "pointInTimeRequired": True,
        "coverageStartDate": coverage_start,
        "processedGameCount": len(records),
        "eligibleGameCount": eligible,
        "ineligibleGameCount": len(records) - eligible,
        "supportedCanonicalGameCount": supported_count,
        "unsupportedCanonicalGameCount": unsupported_count,
        "remainingSupportedGameCount": remaining_supported,
        "totalCanonicalGameCount": len(canonical_games),
        "remainingGameCount": max(0, len(canonical_games) - len(records)),
        "trainingCoverage": round(eligible / len(canonical_games), 8)
        if canonical_games
        else 0.0,
        "supportedTrainingCoverage": round(eligible / supported_count, 8)
        if supported_count
        else 0.0,
        "productionAuthorityChanged": False,
        "records": records,
    }
    manifest["manifestDigest"] = overlay.manifest_digest(manifest)

    pointer = None
    active_revision = previous_revision
    blockers: List[str] = []
    if eligible > 0 and new_records:
        body = core._json_bytes(manifest, pretty=True)
        key = f"mlb/v8/historical-bbs/manifests/{manifest['manifestDigest']}.json"
        pointer = core._put_immutable(s3, bucket, key, body)
        active_revision = core._activate(
            table, pointer, manifest, previous_revision
        )
    elif eligible <= 0:
        blockers.append("no_training_eligible_bbd_prior_game_rows")
    if new_records and new_eligible == 0:
        blockers.append("current_batch_added_zero_training_eligible_bbd_prior_game_rows")

    report: Dict[str, Any] = {
        "proofType": REPORT_TYPE,
        "version": VERSION,
        "featureVersion": prior_game.VERSION,
        "createdAtUtc": created.isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "authority": overlay.AUTHORITY,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "selectionUsedOutcomes": False,
        "targetGameOutcomeUsed": False,
        "sameDayResultsExcluded": True,
        "pointInTimeRequired": True,
        "provider": "bigballsdata",
        "coverageStartDate": coverage_start,
        "historyDays": history_days,
        "historyStartDate": history_start,
        "historyEndDate": history_end,
        "queriedDateCount": len(queried_dates),
        "providerCallsMade": provider_calls,
        "providerRowsReturned": len(history_rows),
        "completedProviderRowsUsed": len(history_material),
        "providerHistoryFingerprint": history_fingerprint,
        "selectedGameCount": len(selected),
        "newRecordCount": len(new_records),
        "newEligibleGameCount": new_eligible,
        "processedGameCount": len(records),
        "eligibleGameCount": eligible,
        "ineligibleGameCount": len(records) - eligible,
        "supportedCanonicalGameCount": supported_count,
        "unsupportedCanonicalGameCount": unsupported_count,
        "remainingSupportedGameCount": remaining_supported,
        "remainingGameCount": manifest["remainingGameCount"],
        "trainingCoverage": manifest["trainingCoverage"],
        "supportedTrainingCoverage": manifest["supportedTrainingCoverage"],
        "targetCrosswalkCount": len(target_crosswalk),
        "crosswalkQuarantineCount": len(quarantine),
        "eligibilityErrorCounts": dict(sorted(eligibility_error_counts.items())),
        "manifest": pointer,
        "manifestDigest": manifest["manifestDigest"],
        "activePointerRevision": active_revision,
        "blockers": sorted(set(blockers)),
        "ok": not blockers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--table-name", default=core.DEFAULT_TABLE)
    parser.add_argument("--historical-stack", default=core.DEFAULT_HISTORICAL_STACK)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS)
    parser.add_argument("--coverage-start", default=DEFAULT_COVERAGE_START)
    parser.add_argument("--start-tolerance-minutes", type=int, default=5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        report = run(
            region=args.region,
            table_name=args.table_name,
            historical_stack=args.historical_stack,
            batch_size=args.batch_size,
            history_days=args.history_days,
            coverage_start=args.coverage_start,
            start_tolerance_minutes=args.start_tolerance_minutes,
            output=Path(args.output),
        )
    except Exception as exc:
        report = {
            "proofType": REPORT_TYPE,
            "version": VERSION,
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "authority": overlay.AUTHORITY,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
            "selectionUsedOutcomes": False,
            "targetGameOutcomeUsed": False,
            "sameDayResultsExcluded": True,
            "pointInTimeRequired": True,
            "blockers": [f"{type(exc).__name__}:{str(exc)[:500]}"],
            "ok": False,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
