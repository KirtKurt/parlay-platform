from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

import mlb_prospective_row_repair as prospective_row_repair
import mlb_terminal_identity_resolution_patch as terminal_identity_resolution


VERSION = "MLB-TERMINAL-LIFECYCLE-COUNT-RECONCILIATION-v2-split-quarantine"
# These states are terminal without an immutable winner. They may prove that a
# game's T-minus-45 lifecycle is accounted for, but they never become a pick,
# training row, promotion artifact, or wagering authority.
MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED = (
    "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
)
TERMINAL_NO_WINNER_STATUSES = frozenset(
    {
        "LOCKED_NO_PREDICTION_DATA",
        MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED,
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
    no_prediction_data: int,
    quarantine_count: int,
) -> None:
    locked_statuses = (
        locked_predictions + no_prediction_data + quarantine_count
    )
    status_complete = bool(game_count and locked_statuses == game_count)
    canonical_complete = bool(game_count and locked_predictions == game_count)
    summary.update(
        {
            "lockedPredictionCount": locked_predictions,
            "canonicalPredictionCount": locked_predictions,
            "lockedStatusCount": locked_statuses,
            "lockOutcomeCount": locked_statuses,
            "noPredictionDataCount": no_prediction_data,
            "missedLockValidPrelockQuarantineCount": quarantine_count,
            "terminalExcludedCount": (
                no_prediction_data + quarantine_count
            ),
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
    no_prediction_data = 0
    quarantine_count = 0
    missed_count = 0
    for row in rows:
        winner_present = row.get("predictedWinner") not in (None, "")
        locked_prediction = row.get("lockedPrediction") is True
        status = _status(row)
        if locked_prediction and winner_present:
            locked_predictions += 1
            continue
        if (
            not winner_present
            and status
            in {
                "LOCKED_NO_PREDICTION_DATA",
                "POSTPONED",
                "CANCELLED",
                "CANCELED",
            }
        ):
            # Resolved no-winner schedule outcomes retain their historical
            # lifecycle coverage semantics.  Only the explicit valid-prelock
            # missed-lock state is counted as quarantine.
            no_prediction_data += 1
        elif (
            not winner_present
            and status
            == MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED
        ):
            quarantine_count += 1
        elif (
            not winner_present
            and status in {"MISSED_NOT_BACKFILLED", "MISSED_LOCK"}
        ):
            # Legacy unresolved misses remain visible but cannot masquerade as
            # either a proven no-data outcome or the explicit quarantine.
            missed_count += 1

    locked_statuses = (
        locked_predictions + no_prediction_data + quarantine_count
    )
    status_complete = bool(game_count and locked_statuses == game_count)
    canonical_complete = bool(game_count and locked_predictions == game_count)

    out.update(
        {
            "gameCount": game_count,
            "lockedPredictionCount": locked_predictions,
            "lockedStatusCount": locked_statuses,
            "lockOutcomeCount": locked_statuses,
            "noPredictionDataCount": no_prediction_data,
            "missedLockValidPrelockQuarantineCount": quarantine_count,
            "terminalExcludedCount": (
                no_prediction_data + quarantine_count
            ),
            "lockStatusComplete": status_complete,
            "canonicalPredictionComplete": canonical_complete,
            "allGamesPredicted": canonical_complete,
            "lockedAny": locked_predictions > 0,
            "partiallyLocked": bool(0 < locked_predictions < game_count),
            "predictionDataUnavailable": no_prediction_data > 0,
            "missedLockOperationalDefect": quarantine_count > 0,
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
            "COMPLETE"
            if canonical_complete
            else "COMPLETE_WITH_MISSED_LOCK_QUARANTINE"
            if quarantine_count
            else "COMPLETE_WITH_NO_PREDICTION_DATA"
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
                no_prediction_data=no_prediction_data,
                quarantine_count=quarantine_count,
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


terminal_identity_resolution.apply(prospective_row_repair)
prospective_row_repair.install()
