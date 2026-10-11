from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.parse

from scripts.mlb_deploy_cutoff_smoke_policy import (
    ALLOWED_POST_CUTOFF_STATUSES,
    all_game_cutoffs_passed,
    historical_lifecycle_acceptance,
)
from scripts.mlb_deploy_http_probe import fetch_json_object


def _fetch(url: str) -> dict[str, Any]:
    return fetch_json_object(
        url,
        max_wait_seconds=180,
        request_timeout_seconds=45,
        retry_delay_seconds=4,
        max_attempts=2,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-lock-smoke-live-diagnostic/1.0",
        },
    )


def _identity(row: dict[str, Any]) -> str:
    return str(row.get("gameId") or row.get("gameIdentity") or "")


def _winner(row: dict[str, Any]) -> str:
    return str(row.get("predictedWinner") or "").strip()


def _lock_status(row: dict[str, Any]) -> str:
    return str(
        row.get("lockStatus")
        or row.get("officialPredictionStatus")
        or ((row.get("perGameCanonicalLock") or {}).get("status"))
        or ""
    ).strip().upper()


def _authoritative(row: dict[str, Any]) -> bool:
    return (
        row.get("lockedPrediction") is True
        if bool(_winner(row))
        else _lock_status(row) in ALLOWED_POST_CUTOFF_STATUSES
    )


def _row_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "gameId": _identity(row),
        "officialGamePk": row.get("officialGamePk"),
        "commenceTime": row.get("commenceTime") or row.get("commence_time"),
        "lockStatus": row.get("lockStatus"),
        "officialPredictionStatus": row.get("officialPredictionStatus"),
        "canonicalLockStatus": (row.get("perGameCanonicalLock") or {}).get("status"),
        "state": row.get("state"),
        "lockedPrediction": row.get("lockedPrediction"),
        "winnerPresent": bool(_winner(row)),
        "probabilityContractVersion": row.get("probabilityContractVersion"),
        "trainingEligible": row.get("trainingEligible"),
        "blocked": row.get("blocked"),
        "lateBackfill": row.get("lateBackfill") or row.get("lateBackfillPerformed"),
        "authoritativeForHistoricalSmoke": _authoritative(row),
    }


def diagnose(api_url: str, *, source_sha: str | None = None) -> dict[str, Any]:
    base_url = api_url.rstrip("/")
    status = _fetch(base_url + "/v1/mlb/locks/status")
    slate = str(status.get("slateDateEt") or "")
    query = urllib.parse.urlencode({"date": slate})
    predictions = _fetch(base_url + "/v1/mlb/predictions?" + query)

    status_rows = [
        row for row in (status.get("perGameStatus") or [])
        if isinstance(row, dict)
    ]
    prediction_rows = [
        row for row in (predictions.get("predictions") or [])
        if isinstance(row, dict)
    ]
    game_count = int(status.get("gameCount") or 0)
    now = datetime.now(timezone.utc)

    rejection_reasons: list[str] = []
    if game_count <= 0:
        rejection_reasons.append("game_count_not_positive")
    if len(status_rows) != game_count:
        rejection_reasons.append("status_row_count_mismatch")
    if not all_game_cutoffs_passed(status_rows, now=now):
        rejection_reasons.append("not_all_tminus45_cutoffs_passed")
    status_ids = [_identity(row) for row in status_rows]
    if any(not value for value in status_ids):
        rejection_reasons.append("status_identity_missing")
    if len(set(status_ids)) != game_count:
        rejection_reasons.append("status_identity_not_unique")
    non_authoritative = [
        _identity(row) for row in status_rows if not _authoritative(row)
    ]
    if non_authoritative:
        rejection_reasons.append("non_authoritative_status_rows")
    if not isinstance(predictions.get("predictions"), list):
        rejection_reasons.append("predictions_rows_not_list")
    if predictions.get("sport") not in (None, "", "mlb"):
        rejection_reasons.append("predictions_sport_mismatch")

    authoritative_locked = {
        _identity(row): _winner(row)
        for row in status_rows
        if row.get("lockedPrediction") is True and bool(_winner(row))
    }
    contradictory_locked: list[str] = []
    for row in prediction_rows:
        if row.get("lockedPrediction") is True:
            row_identity = _identity(row)
            row_winner = _winner(row)
            if (
                not row_identity
                or not row_winner
                or authoritative_locked.get(row_identity) != row_winner
            ):
                contradictory_locked.append(row_identity)
    if contradictory_locked:
        rejection_reasons.append("prediction_locked_winner_conflicts_with_status")

    projected = copy.deepcopy(predictions)
    accepted = historical_lifecycle_acceptance(
        projected,
        status_rows,
        game_count,
        now=now,
    )
    winner_rows = [row for row in prediction_rows if bool(_winner(row))]
    prelock_winner_rows = [
        row for row in winner_rows if row.get("lockedPrediction") is not True
    ]
    all_prelock_probability_contract = bool(prelock_winner_rows) and all(
        row.get("probabilityContractVersion")
        == "MLB-PREDICTION-PROBABILITY-CONTRACT-v1-canonical-model-direction"
        for row in prelock_winner_rows
    )
    all_winners_locked = bool(winner_rows) and not prelock_winner_rows

    return {
        "proofType": "MLB_LOCK_STATUS_SMOKE_LIVE_DIAGNOSTIC",
        "createdAtUtc": now.isoformat(),
        "readOnly": True,
        "sourceSha": source_sha,
        "slateDateEt": slate,
        "gameCount": game_count,
        "historicalLifecycleAccepted": accepted,
        "deployLoopWouldBreak": bool(
            accepted or all_winners_locked or all_prelock_probability_contract
        ),
        "deployLoopBranches": {
            "historicalLifecycle": accepted,
            "allWinnerRowsAlreadyLocked": all_winners_locked,
            "allUnlockedWinnerRowsHaveProbabilityContract": (
                all_prelock_probability_contract
            ),
        },
        "historicalLifecycleRejectionReasons": rejection_reasons,
        "nonAuthoritativeStatusGameIds": non_authoritative,
        "contradictoryLockedPredictionGameIds": contradictory_locked,
        "statusSummary": {
            key: status.get(key)
            for key in (
                "ok",
                "sport",
                "officialScheduleBacked",
                "officialScheduleAuthorityVersion",
                "officialScheduleAuthoritativeStartTimes",
                "lockedPredictionCount",
                "lockedStatusCount",
                "noPredictionDataCount",
                "lockStatusComplete",
                "canonicalPredictionComplete",
                "operationalDefect",
                "statusDetail",
            )
        },
        "predictionSummary": {
            key: predictions.get(key)
            for key in (
                "ok",
                "sport",
                "gameCount",
                "lockedPredictionCount",
                "officialPredictionCount",
                "lockedStatusCount",
                "noPredictionDataCount",
                "lockStatusComplete",
                "canonicalPredictionComplete",
                "operationalDefect",
            )
        },
        "counts": {
            "statusRows": len(status_rows),
            "predictionRows": len(prediction_rows),
            "winnerRows": len(winner_rows),
            "unlockedWinnerRows": len(prelock_winner_rows),
            "statusAuthoritativeRows": len(status_rows) - len(non_authoritative),
            "statusLockedWinners": len(authoritative_locked),
        },
        "statusRows": [_row_view(row) for row in status_rows],
        "predictionRows": [_row_view(row) for row in prediction_rows],
        "secretExposed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-sha")
    args = parser.parse_args()

    report = diagnose(args.api_url, source_sha=args.source_sha)
    Path(args.output).write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "historicalLifecycleAccepted": report["historicalLifecycleAccepted"],
        "deployLoopWouldBreak": report["deployLoopWouldBreak"],
        "rejectionReasons": report["historicalLifecycleRejectionReasons"],
        "counts": report["counts"],
    }, indent=2))


if __name__ == "__main__":
    main()
