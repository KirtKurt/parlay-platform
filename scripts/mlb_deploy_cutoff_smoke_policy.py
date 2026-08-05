from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional


VERSION = "MLB-DEPLOY-CUTOFF-SMOKE-POLICY-v4-status-authoritative-projection"
LOCK_MINUTES_BEFORE_GAME = 45
ALLOWED_POST_CUTOFF_STATUSES = frozenset({
    "MISSED_LOCK",
    "MISSED_NOT_BACKFILLED",
    "LOCK_DUE_CANONICAL_MISSING",
    "LOCKED_NO_PREDICTION_DATA",
    "POSTPONED",
    "CANCELLED",
    "CANCELED",
})


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_status(row: Dict[str, Any]) -> str:
    return str(
        row.get("lockStatus")
        or row.get("officialPredictionStatus")
        or ((row.get("perGameCanonicalLock") or {}).get("status"))
        or ""
    ).strip().upper()


def _row_identity(row: Dict[str, Any]) -> str:
    return str(row.get("gameId") or row.get("gameIdentity") or "").strip()


def _winner(row: Dict[str, Any]) -> str:
    return str(row.get("predictedWinner") or "").strip()


def _zero_count(value: Any) -> bool:
    try:
        return int(value or 0) == 0
    except (TypeError, ValueError):
        return False


def all_game_cutoffs_passed(
    status_rows: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> bool:
    rows = [row for row in status_rows if isinstance(row, dict)]
    if not rows:
        return False
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoffs = []
    for row in rows:
        start = _parse_utc(row.get("commenceTime") or row.get("commence_time"))
        if start is None:
            return False
        cutoffs.append(start - timedelta(minutes=LOCK_MINUTES_BEFORE_GAME))
    return bool(cutoffs) and all(observed_at >= cutoff for cutoff in cutoffs)


def _status_row_is_authoritative(row: Dict[str, Any]) -> bool:
    winner = _winner(row)
    if winner:
        return row.get("lockedPrediction") is True
    return _row_status(row) in ALLOWED_POST_CUTOFF_STATUSES


def _project_authoritative_status(
    predictions: Dict[str, Any],
    status: list[Dict[str, Any]],
    game_count: int,
) -> None:
    projected_rows = copy.deepcopy(status)
    locked_predictions = sum(
        1
        for row in projected_rows
        if row.get("lockedPrediction") is True and bool(_winner(row))
    )
    terminal_no_winner = game_count - locked_predictions
    ignored_rows = [
        row
        for row in (predictions.get("predictions") or [])
        if isinstance(row, dict)
        and bool(_winner(row))
        and row.get("lockedPrediction") is not True
    ]
    predictions.update({
        "sport": "mlb",
        "gameCount": game_count,
        "predictions": projected_rows,
        "displayStatusCoverageComplete": True,
        "lifecycleCoverageComplete": True,
        "lockedPredictionCount": locked_predictions,
        "officialPredictionCount": locked_predictions,
        "lockedStatusCount": game_count,
        "noPredictionDataCount": terminal_no_winner,
        "lockStatusComplete": True,
        "canonicalPredictionComplete": locked_predictions == game_count,
        "operationalDefect": bool(predictions.get("operationalDefect", True)),
        "statusOnlyHistoricalProjection": locked_predictions == 0,
        "statusOnlyHistoricalProjectionVersion": VERSION,
        "statusOnlyHistoricalProjectionPersisted": False,
        "statusAuthoritativeHistoricalProjection": True,
        "statusAuthoritativeHistoricalProjectionVersion": VERSION,
        "statusAuthoritativeHistoricalProjectionPersisted": False,
        "ignoredNonAuthoritativeWinnerCount": len(ignored_rows),
    })


def historical_lifecycle_acceptance(
    predictions: Dict[str, Any],
    status_rows: Iterable[Dict[str, Any]],
    game_count: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Verify a post-cutoff slate from immutable lock lifecycle evidence.

    Before the final T-minus-45 cutoff this helper always returns False. After
    every cutoff, the lock-status rows become the verification authority: each
    official game must have one unique row that is either an immutable locked
    prediction or an explicit terminal no-winner status. Prediction-endpoint
    rows are never promoted here. Stale, unlocked winners may be ignored only
    when the lock-status authority proves the complete slate and aggregate
    fields make no contradictory immutable-winner claim.

    The accepted evidence is projected only into the in-process smoke payload;
    no DynamoDB row, winner, probability, or production pointer is written.
    """

    if game_count <= 0 or not isinstance(predictions, dict):
        return False

    status = [row for row in status_rows if isinstance(row, dict)]
    if len(status) != game_count or not all_game_cutoffs_passed(status, now=now):
        return False
    status_ids = [_row_identity(row) for row in status]
    if any(not identity for identity in status_ids) or len(set(status_ids)) != game_count:
        return False
    if not all(_status_row_is_authoritative(row) for row in status):
        return False

    raw_rows = predictions.get("predictions")
    if not isinstance(raw_rows, list):
        return False
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) != len(raw_rows):
        return False
    if predictions.get("sport") not in (None, "", "mlb"):
        return False

    authoritative_locked = {
        _row_identity(row): _winner(row)
        for row in status
        if row.get("lockedPrediction") is True and bool(_winner(row))
    }
    if not rows:
        if not (
            _zero_count(predictions.get("lockedPredictionCount"))
            and _zero_count(predictions.get("officialPredictionCount"))
            and predictions.get("canonicalPredictionComplete") is not True
        ):
            return False
    elif predictions.get("canonicalPredictionComplete") is True and len(authoritative_locked) != game_count:
        return False

    # If the prediction endpoint claims an immutable winner, it must exactly
    # match the lock-status authority. Unlocked winners are non-authoritative
    # after cutoff and may not block a read-only deployment verification.
    for row in rows:
        identity = _row_identity(row)
        winner = _winner(row)
        if row.get("lockedPrediction") is True:
            if not identity or authoritative_locked.get(identity) != winner or not winner:
                return False

    _project_authoritative_status(predictions, status, game_count)
    return True
