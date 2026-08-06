from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

import mlb_prospective_row_repair as prospective_row_repair


VERSION = "MLB-TERMINAL-LIFECYCLE-COUNT-RECONCILIATION-v1-row-derived"
# These states are terminal without an immutable winner. They may prove that a
# game's T-minus-45 lifecycle is accounted for, but they never become a pick,
# training row, promotion artifact, or wagering authority.
TERMINAL_NO_WINNER_STATUSES = frozenset(
    {
        "LOCKED_NO_PREDICTION_DATA",
        "MISSED_NOT_BACKFILLED",
        "MISSED_LOCK",
        "POSTPONED",
        "CANCELLED",
        "CANCELED",
    }
)


def _status(row: Dict[str, Any]) -> str:
    return str(
        row.get("lockStatus")
        or row.get("state")
        or row.get("officialPredictionStatus")
        or ((row.get("perGameCanonicalLock") or {}).get("status"))
        or ""
    ).strip().upper()


def _identity(row: Dict[str, Any]) -> str:
    return str(row.get("gameId") or row.get("gameIdentity") or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_rows(payload: Dict[str, Any], row_field: str) -> Optional[List[Dict[str, Any]]]:
    raw_rows = payload.get(row_field)
    if not isinstance(raw_rows, list):
        return None
    rows = [row for row in raw_rows if isinstance(row, dict)]
    if len(rows) != len(raw_rows):
        return None
    game_count = _int(payload.get("gameCount"), len(rows))
    identities = [_identity(row) for row in rows]
    if (
        game_count <= 0
        or len(rows) != game_count
        or any(not identity for identity in identities)
        or len(set(identities)) != game_count
    ):
        return None
    return rows


def _update_summary(
    summary: Dict[str, Any],
    *,
    game_count: int,
    locked_predictions: int,
    terminal_no_winner: int,
) -> None:
    locked_statuses = locked_predictions + terminal_no_winner
    status_complete = bool(game_count and locked_statuses == game_count)
    canonical_complete = bool(game_count and locked_predictions == game_count)
    summary.update(
        {
            "lockedPredictionCount": locked_predictions,
            "canonicalPredictionCount": locked_predictions,
            "lockedStatusCount": locked_statuses,
            "lockOutcomeCount": locked_statuses,
            "noPredictionDataCount": terminal_no_winner,
            "lockStatusComplete": status_complete,
            "canonicalPredictionComplete": canonical_complete,
            "pendingCanonicalGameCount": max(game_count - locked_predictions, 0),
            "pendingLockStatusGameCount": max(game_count - locked_statuses, 0),
            "terminalLifecycleCountReconciliationVersion": VERSION,
            "terminalLifecycleCountsDerivedFromRows": True,
        }
    )


def reconcile_payload(
    payload: Dict[str, Any],
    *,
    row_field: str,
) -> Dict[str, Any]:
    """Reconcile public aggregate counts from exact per-game lifecycle rows.

    This function is deliberately read-only with respect to prediction
    authority. It never adds or rewrites a row, winner, probability, feature,
    artifact, promotion pointer, or wager flag. It only makes aggregate counts
    agree with the already-returned one-row-per-game lifecycle evidence.

    Invalid, partial, duplicate, or identity-less row sets are returned
    unchanged so a transport or roster defect cannot be hidden by this layer.
    """

    if not isinstance(payload, dict):
        return payload
    out = copy.deepcopy(payload)
    rows = _valid_rows(out, row_field)
    if rows is None:
        return out

    game_count = len(rows)
    locked_predictions = 0
    terminal_no_winner = 0
    missed_count = 0
    for row in rows:
        winner_present = row.get("predictedWinner") not in (None, "")
        locked_prediction = row.get("lockedPrediction") is True
        status = _status(row)
        if locked_prediction and winner_present:
            locked_predictions += 1
            continue
        if not winner_present and status in TERMINAL_NO_WINNER_STATUSES:
            terminal_no_winner += 1
            if status in {"MISSED_NOT_BACKFILLED", "MISSED_LOCK"}:
                missed_count += 1

    locked_statuses = locked_predictions + terminal_no_winner
    status_complete = bool(game_count and locked_statuses == game_count)
    canonical_complete = bool(game_count and locked_predictions == game_count)

    out.update(
        {
            "gameCount": game_count,
            "lockedPredictionCount": locked_predictions,
            "lockedStatusCount": locked_statuses,
            "lockOutcomeCount": locked_statuses,
            "noPredictionDataCount": terminal_no_winner,
            "lockStatusComplete": status_complete,
            "canonicalPredictionComplete": canonical_complete,
            "allGamesPredicted": canonical_complete,
            "lockedAny": locked_predictions > 0,
            "partiallyLocked": bool(0 < locked_predictions < game_count),
            "predictionDataUnavailable": terminal_no_winner > 0,
            "lockOutcomeCoveragePct": round(
                locked_statuses / game_count * 100.0, 2
            ),
            "officialPredictionCoveragePct": round(
                locked_predictions / game_count * 100.0, 2
            ),
            "terminalLifecycleCountReconciliationVersion": VERSION,
            "terminalLifecycleCountsDerivedFromRows": True,
            "terminalLifecycleCountsReconciled": True,
        }
    )
    if "canonicalPredictionCount" in out:
        out["canonicalPredictionCount"] = locked_predictions
    if "officialPredictionCount" in out:
        out["officialPredictionCount"] = locked_predictions
    if "officialPickCount" in out:
        out["officialPickCount"] = locked_predictions
    if "pendingGameCount" in out:
        out["pendingGameCount"] = max(game_count - locked_statuses, 0)
    if "pendingLockStatusGameCount" in out:
        out["pendingLockStatusGameCount"] = max(
            game_count - locked_statuses, 0
        )
    if "missedGameCount" in out:
        out["missedGameCount"] = missed_count
    if "missedLockCount" in out:
        out["missedLockCount"] = missed_count
    if "dailyCardComplete" in out:
        out["dailyCardComplete"] = status_complete
    if "slateLockStatus" in out and status_complete:
        out["slateLockStatus"] = (
            "COMPLETE" if canonical_complete else "COMPLETE_WITH_NO_PREDICTION_DATA"
        )

    for key in (
        "slateCoverage",
        "publicPerGameAuthority",
        "lastPossiblePredictionGate",
        "slatePredictionLock",
    ):
        summary = out.get(key)
        if isinstance(summary, dict):
            summary = copy.deepcopy(summary)
            _update_summary(
                summary,
                game_count=game_count,
                locked_predictions=locked_predictions,
                terminal_no_winner=terminal_no_winner,
            )
            out[key] = summary

    # Preserve any operational defect. Count reconciliation proves only that
    # the response is internally coherent; it does not excuse a missed lock.
    return out


def reconcile_http_response(
    response: Dict[str, Any],
    *,
    row_field: str,
) -> Dict[str, Any]:
    if not isinstance(response, dict):
        return response
    out = dict(response)
    body = out.get("body")
    try:
        payload = json.loads(body) if isinstance(body, str) else dict(body or {})
    except Exception:
        return out
    if not isinstance(payload, dict):
        return out
    out["body"] = json.dumps(
        reconcile_payload(payload, row_field=row_field), default=str
    )
    return out


prospective_row_repair.install()
