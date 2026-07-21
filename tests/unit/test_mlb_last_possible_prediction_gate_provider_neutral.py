from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_last_possible_prediction_gate as gate


def _complete_snapshot() -> dict:
    return {
        "version": "test-fundamentals-v2",
        "fingerprint": "test-fingerprint",
        "pregameComplete": True,
        "trainingEligibleAtCapture": True,
        "completenessRatio": 1.0,
        "missingGroups": [],
    }


@pytest.fixture(autouse=True)
def validated_snapshot_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gate,
        "fundamentals_v2",
        SimpleNamespace(validate=lambda snapshot: []),
    )


@pytest.mark.parametrize(
    ("final_locked", "fundamentals_complete", "expected_full_data"),
    (
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ),
)
def test_full_data_final_pick_requires_time_lock_and_validated_fundamentals_v2(
    final_locked: bool,
    fundamentals_complete: bool,
    expected_full_data: bool,
) -> None:
    row = {
        "gameId": "game-1",
        "officialPick": True,
        "fullDataFinalPick": True,
        "winnerOptimizer": {"fundamentalsApplied": True},
        "bbsShadowCapture": {
            "ok": True,
            "shadowOnly": True,
            "trainingEligible": False,
        },
        "slatePredictionLock": {
            "slateWideLock": True,
            "locked": final_locked,
            "lockMinutesBeforeFirstGame": 45,
        },
    }
    if fundamentals_complete:
        row["fundamentalsSnapshotV2"] = _complete_snapshot()

    result = gate.annotate_prediction(row)
    status = result["lastPossiblePredictionGate"]

    assert status["finalLocked"] is final_locked
    assert status["timeLockFinal"] is final_locked
    assert status["fundamentalsV2Applied"] is fundamentals_complete
    assert status["fundamentalsV2Complete"] is fundamentals_complete
    assert status["fullDataFinalPick"] is expected_full_data
    assert result["fullDataFinalPick"] is expected_full_data
    assert result["officialPick"] is True
    assert "finalGateBlocked" not in status
    assert "finalGateBlockReason" not in status


def test_shadow_context_and_generic_optimizer_flag_do_not_earn_v2_credit() -> None:
    result = gate.annotate_prediction(
        {
            "gameId": "shadow-only",
            "winnerOptimizer": {"fundamentalsApplied": True},
            "bbsShadowCapture": {
                "status": "CAPTURED",
                "shadowOnly": True,
                "trainingEligible": False,
                "completenessRatio": 1.0,
            },
            "slatePredictionLock": {
                "slateWideLock": True,
                "locked": True,
            },
        }
    )

    status = result["lastPossiblePredictionGate"]
    assert status["timeLockFinal"] is True
    assert status["fundamentalsV2Applied"] is False
    assert status["fundamentalsV2ValidationReasons"] == [
        "fundamentals_v2_snapshot_missing"
    ]
    assert result["fullDataFinalPick"] is False
    assert "FINAL_LOCKED_WITHOUT_COMPLETE_FUNDAMENTALS_V2" in result["tags"]


def test_invalid_v2_snapshot_is_not_credited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gate.fundamentals_v2,
        "validate",
        lambda snapshot: ["fundamentals_v2_fingerprint_mismatch"],
    )

    result = gate.annotate_prediction(
        {
            "gameId": "invalid-v2",
            "fundamentalsSnapshotV2": _complete_snapshot(),
            "slatePredictionLock": {
                "slateWideLock": True,
                "locked": True,
            },
        }
    )

    status = result["lastPossiblePredictionGate"]
    assert status["fundamentalsV2Applied"] is False
    assert status["fundamentalsV2ValidationReasons"] == [
        "fundamentals_v2_fingerprint_mismatch"
    ]
    assert result["fullDataFinalPick"] is False


def test_result_summary_separates_finality_from_v2_completeness() -> None:
    lock = {"slateWideLock": True, "locked": True}
    result = gate.annotate_result(
        {
            "modelVersion": "model-test",
            "slatePredictionLock": lock,
            "predictions": [
                {
                    "gameId": "complete",
                    "fundamentalsSnapshotV2": _complete_snapshot(),
                    "slatePredictionLock": lock,
                },
                {
                    "gameId": "incomplete",
                    "slatePredictionLock": lock,
                },
            ],
        }
    )

    summary = result["lastPossiblePredictionGate"]
    assert summary["finalLockedCount"] == 2
    assert summary["timeLockFinalCount"] == 2
    assert summary["fundamentalsV2AppliedCount"] == 1
    assert summary["fundamentalsV2IncompleteAtFinalLockCount"] == 1
    assert summary["fullDataFinalPickCount"] == 1
    assert summary["timeLockFinalityIndependentFromFundamentalsV2"] is True
    assert summary["shadowEvidenceCreditedForFundamentalsV2"] is False
    assert [row["fullDataFinalPick"] for row in result["predictions"]] == [
        True,
        False,
    ]


def test_active_gate_source_contains_no_retired_provider_contract() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8").lower()
    retired_provider_token = "sports" + "dataio"

    assert retired_provider_token not in source
    assert retired_provider_token not in json.dumps(
        gate.annotate_result({"predictions": []}), sort_keys=True
    ).lower()
