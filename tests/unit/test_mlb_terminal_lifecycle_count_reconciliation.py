from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_terminal_lifecycle_count_reconciliation as reconciliation


def _payload(*rows):
    return {
        "gameCount": len(rows),
        "games": list(rows),
        "slateLockStatus": "PENDING",
        "missedGameCount": 99,
        "slateCoverage": {},
    }


def test_quarantine_is_distinct_from_no_data_and_canonical_counts():
    result = reconciliation.reconcile_payload(
        _payload(
            {
                "gameId": "game-1",
                "lockedPrediction": True,
                "predictedWinner": "Home",
                "lockStatus": "LOCKED",
            },
            {
                "gameId": "game-2",
                "lockedPrediction": False,
                "lockStatus": "LOCKED_NO_PREDICTION_DATA",
            },
            {
                "gameId": "game-3",
                "lockedPrediction": False,
                "lockStatus": (
                    reconciliation.MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED
                ),
            },
            {
                "gameId": "game-4",
                "lockedPrediction": False,
                "lockStatus": "POSTPONED",
            },
        ),
        row_field="games",
    )

    assert result["lockedPredictionCount"] == 1
    assert result["noPredictionDataCount"] == 2
    assert result["missedLockValidPrelockQuarantineCount"] == 1
    assert result["terminalExcludedCount"] == 3
    assert result["lockOutcomeCount"] == 4
    assert result["lockedStatusCount"] == 4
    assert result["lockStatusComplete"] is True
    assert result["canonicalPredictionComplete"] is False
    assert result["slateLockStatus"] == "COMPLETE_WITH_MISSED_LOCK_QUARANTINE"
    assert result["slateCoverage"]["noPredictionDataCount"] == 2
    assert result["slateCoverage"]["missedLockValidPrelockQuarantineCount"] == 1
    assert result["slateCoverage"]["lockOutcomeCount"] == 4


@pytest.mark.parametrize("status", ["POSTPONED", "CANCELLED", "CANCELED"])
def test_resolved_no_winner_schedule_status_remains_lifecycle_complete(status):
    result = reconciliation.reconcile_payload(
        _payload(
            {
                "gameId": "game-1",
                "lockedPrediction": False,
                "lockStatus": status,
            }
        ),
        row_field="games",
    )

    assert result["noPredictionDataCount"] == 1
    assert result["missedLockValidPrelockQuarantineCount"] == 0
    assert result["lockOutcomeCount"] == 1
    assert result["lockStatusComplete"] is True
    assert result["slateLockStatus"] == "COMPLETE_WITH_NO_PREDICTION_DATA"


def test_unresolved_legacy_miss_is_not_relabelled_as_no_data_or_quarantine():
    result = reconciliation.reconcile_payload(
        _payload(
            {
                "gameId": "game-1",
                "lockedPrediction": False,
                "lockStatus": "MISSED_LOCK",
            }
        ),
        row_field="games",
    )

    assert result["noPredictionDataCount"] == 0
    assert result["missedLockValidPrelockQuarantineCount"] == 0
    assert result["lockOutcomeCount"] == 0
    assert result["lockStatusComplete"] is False
    assert result["missedGameCount"] == 1
