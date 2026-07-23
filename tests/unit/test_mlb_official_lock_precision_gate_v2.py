from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_lock_quality_gate as gate


def _module():
    module = SimpleNamespace()
    module._is_official = lambda row: bool(row.get("canonical"))
    module._selected_signal = lambda row: row.get("awaySignal") or {}
    module._team_probability = lambda row: row.get("teamWinProbabilityPct") / 100.0
    return module


def _row(probability: float = 62.0, **signal):
    return {
        "canonical": True,
        "predictedWinner": "Away Team",
        "predictedSide": "away",
        "teamWinProbabilityPct": probability,
        "tags": [],
        "awaySignal": {
            "delta": 0.01,
            "reversalCount": 0,
            "bookDivergence": 0.01,
            "tags": [],
            **signal,
        },
    }


def test_compatibility_mode_preserves_existing_quality_gate_behavior(monkeypatch) -> None:
    monkeypatch.delenv(gate.PRECISION_ENFORCEMENT_ENV, raising=False)
    module = _module()
    gate.apply(module)
    row = _row(62.0)
    assert module._is_official(row) is True
    assert row["officialLockQualityGate"]["precisionAdmissionEnforced"] is False


def test_runtime_enforcement_abstains_without_trusted_70pct_record(monkeypatch) -> None:
    monkeypatch.setenv(gate.PRECISION_ENFORCEMENT_ENV, "true")
    module = _module()
    gate.apply(module)
    row = _row(66.0)
    assert module._is_official(row) is False
    decision = row["officialLockQualityGate"]
    assert decision["visibleLockedPickRetained"] is True
    assert decision["recommendationAbstained"] is True
    assert "precision_admission_not_met" in decision["reasons"]
    assert decision["precisionAdmission"]["futureAccuracyGuaranteed"] is False


def test_late_reversal_toward_selection_is_risk_not_positive_authority(monkeypatch) -> None:
    monkeypatch.delenv(gate.PRECISION_ENFORCEMENT_ENV, raising=False)
    module = _module()
    gate.apply(module)
    row = _row(
        66.0,
        temporalFeatures={
            "horizons": {
                "15m": {"velocityPpHr": 0.1, "reversalCount": 0},
                "60m": {"velocityPpHr": 0.1, "reversalCount": 0},
                "180m": {"velocityPpHr": 0.1, "reversalCount": 1},
                "full": {
                    "velocityPpHr": 0.1,
                    "reversalCount": 1,
                    "pathEfficiency": 0.9,
                    "latestReversalMinutesBeforeEvent": 120.0,
                    "latestLeg": {"amplitudePp": 1.2, "direction": 1},
                    "market": {
                        "eligibleBookCount": 4,
                        "weightedBookDirectionAgreement": 0.8,
                        "latestBookRangePp": 1.0,
                    },
                    "signalQuality": {"signalQualityIndex": 80.0},
                },
            }
        },
    )
    assert module._is_official(row) is False
    assert "late_reversal_direction_risk_without_validated_exception" in row["officialLockQualityGate"]["reasons"]
