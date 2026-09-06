#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_reversal_shape_runtime_patch as patch


def _h(velocity, reversals, duration, pulls, volatility=0.2):
    return {
        "velocityPpHr": velocity,
        "reversalCount": reversals,
        "durationMinutes": duration,
        "pullCount": pulls,
        "coverageRatio": 1.0,
        "maxGapMinutes": 15.0,
        "volatilityPpPerPull": volatility,
    }


def _row():
    return {
        "predictedSide": "home",
        "predictedWinner": "Home Club",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "playable": True,
        "actionablePick": True,
        "officialPick": True,
        "tags": [],
        "homeSignal": {
            "marketConsensusProbability": 0.61,
            "delta": 0.012,
            "reversalCount": 2,
            "temporalFeatures": {
                "horizons": {
                    "15m": _h(-4.0, 1, 15, 2),
                    "60m": _h(0.4, 1, 60, 5),
                    "180m": _h(0.25, 1, 180, 13),
                    "full": _h(0.15, 2, 600, 41),
                }
            },
        },
        "awaySignal": {"marketConsensusProbability": 0.39},
    }


def main() -> int:
    signal_policy = SimpleNamespace(
        _signal_risk_gate_reasons=lambda row: [],
        _components=lambda row: [],
        _apply_row=lambda row: dict(row),
        _display_card=lambda row: {"predictedWinner": row.get("predictedWinner")},
    )
    patch._install_signal_policy(signal_policy)
    guarded = signal_policy._apply_row(_row())
    assert guarded["predictedWinner"] == "Home Club"
    assert guarded["playable"] is False
    assert guarded["actionablePick"] is False
    assert guarded["reversalShapeV1"]["lateOppositeShock"] is True
    assert guarded["signalRiskGate"]["blocked"] is True
    assert "late_opposite_shock_without_confirmation" in guarded["signalRiskGate"]["reasons"]
    assert signal_policy._components(_row())

    feature_module = SimpleNamespace(
        feature_vector=lambda row: {"score": 50.0},
        ML_FEATURES=["score"],
        VERSION="old",
    )
    overlay = SimpleNamespace(feature_vector=feature_module.feature_vector, FEATURE_VECTOR_VERSION="old")
    patch._install_feature_vector(feature_module, overlay)
    vector = overlay.feature_vector(_row())
    assert vector["reversalShapeLateShock"] == 1.0
    assert vector["reversalShapeMaximumCount"] == 2.0
    assert "reversalShapeFullGrossMovePp" in feature_module.ML_FEATURES

    def unvalidated(result):
        for row in result["predictions"]:
            row["mlOverlay"] = {"validatedAgainstTarget": False, "confirmed": False}
        return result

    runtime = SimpleNamespace(enhance_result=unvalidated)
    patch._install_overlay(runtime)
    rejected = runtime.enhance_result({"predictions": [_row()]})["predictions"][0]
    assert rejected["actionablePick"] is False
    assert rejected["officialPick"] is False
    assert rejected["predictedWinner"] == "Home Club"
    assert "UNVALIDATED_ACCURACY_EVIDENCE" in rejected["tags"]

    def validated(result):
        for row in result["predictions"]:
            row.update({"actionablePick": True, "officialPick": True, "playable": True})
            row["mlOverlay"] = {"validatedAgainstTarget": True, "confirmed": True}
        return result

    runtime2 = SimpleNamespace(enhance_result=validated)
    patch._install_overlay(runtime2)
    promoted = runtime2.enhance_result({"predictions": [_row()]})["predictions"][0]
    assert promoted["actionablePick"] is True
    assert promoted["officialPick"] is True

    print("MLB reversal shape runtime blocks unstable or unvalidated picks while preserving visible winners")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
