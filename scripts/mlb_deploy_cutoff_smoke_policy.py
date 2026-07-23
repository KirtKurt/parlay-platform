from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional


VERSION = "MLB-DEPLOY-CUTOFF-SMOKE-POLICY-v1"
LOCK_MINUTES_BEFORE_GAME = 45
ALLOWED_POST_CUTOFF_STATUSES = frozenset({
    "MISSED_LOCK",
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


def historical_lifecycle_acceptance(
    predictions: Dict[str, Any],
    status_rows: Iterable[Dict[str, Any]],
    game_count: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Allow a deploy smoke to finish without fabricating late pregame picks.

    This exception is intentionally narrow. It applies only after every game's
    immutable T-minus-45 cutoff, when both public endpoints expose exactly one
    lifecycle row per durable game, no winner is present, and every row carries
    an explicit post-cutoff status. Before the final cutoff, existing strict
    pre-lock probability-contract checks remain authoritative.
    """

    if game_count <= 0:
        return False
    status = [row for row in status_rows if isinstance(row, dict)]
    rows = [
        row
        for row in (predictions.get("predictions") or [])
        if isinstance(row, dict)
    ]
    if len(status) != game_count or len(rows) != game_count:
        return False
    if predictions.get("displayStatusCoverageComplete") is not True:
        return False
    if predictions.get("lifecycleCoverageComplete") is not True:
        return False
    if not all_game_cutoffs_passed(status, now=now):
        return False
    if any(row.get("predictedWinner") not in (None, "") for row in rows):
        return False
    statuses = {
        str(
            row.get("lockStatus")
            or row.get("officialPredictionStatus")
            or ((row.get("perGameCanonicalLock") or {}).get("status"))
            or ""
        ).strip().upper()
        for row in rows
    }
    if not statuses or not statuses.issubset(ALLOWED_POST_CUTOFF_STATUSES):
        return False
    status_ids = {
        str(row.get("gameId") or row.get("gameIdentity") or "")
        for row in status
    }
    prediction_ids = {
        str(row.get("gameId") or row.get("gameIdentity") or "")
        for row in rows
    }
    return (
        "" not in status_ids
        and "" not in prediction_ids
        and len(status_ids) == game_count
        and status_ids == prediction_ids
    )
