#!/usr/bin/env python3
from __future__ import annotations

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


def main() -> int:
    module = _module()
    gate.apply(module)

    minnesota = {
        "canonical": True,
        "predictedWinner": "Minnesota Twins",
        "predictedSide": "away",
        "teamWinProbabilityPct": 54.69,
        "tags": ["SIGNAL_RISK_GATE_BLOCKED"],
        "awaySignal": {
            "delta": 0.022218,
            "reversalCount": 2,
            "bookDivergence": 0.022307,
            "tags": [],
            "temporalFeatures": {
                "horizons": {
                    "15m": {"velocityPpHr": -0.4, "reversalCount": 1},
                    "60m": {"velocityPpHr": 0.2, "reversalCount": 2},
                    "180m": {"velocityPpHr": 0.1, "reversalCount": 3},
                    "full": {"velocityPpHr": 0.05, "reversalCount": 7},
                }
            },
        },
    }
    assert module._is_official(minnesota) is False
    minnesota_reasons = set(minnesota["officialLockQualityGate"]["reasons"])
    assert "selected_team_probability_below_60pct" in minnesota_reasons
    assert "multiple_reversals_without_independent_confirmation" in minnesota_reasons
    assert "late_direction_conflict_without_independent_confirmation" in minnesota_reasons

    texas = {
        "canonical": True,
        "predictedWinner": "Texas Rangers",
        "predictedSide": "away",
        "teamWinProbabilityPct": 60.16,
        "tags": ["PROBABILITY_DIRECTION_INTEGRITY_CORRECTION"],
        "awaySignal": {
            "delta": -0.005417,
            "reversalCount": 2,
            "bookDivergence": 0.020668,
            "tags": [],
        },
    }
    assert module._is_official(texas) is False
    texas_reasons = set(texas["officialLockQualityGate"]["reasons"])
    assert "movement_against_selected_team" in texas_reasons
    assert "multiple_reversals_without_independent_confirmation" in texas_reasons
    assert "probability_direction_integrity_correction" in texas_reasons

    print("July 21 MLB losing profiles remain visible but are excluded from official target eligibility")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
