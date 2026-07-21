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


def _row(probability: float = 60.0, **signal):
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


def main() -> int:
    module = _module()
    gate.apply(module)

    below_floor = _row(59.99)
    assert module._is_official(below_floor) is False
    assert "selected_team_probability_below_60pct" in below_floor["officialLockQualityGate"]["reasons"]

    at_floor = _row(60.0)
    assert module._is_official(at_floor) is True, at_floor["officialLockQualityGate"]

    movement_against = _row(60.16, delta=-0.005, reversalCount=2)
    movement_against["tags"] = ["PROBABILITY_DIRECTION_INTEGRITY_CORRECTION"]
    assert module._is_official(movement_against) is False
    assert {
        "movement_against_selected_team",
        "multiple_reversals_without_independent_confirmation",
        "probability_direction_integrity_correction",
    }.issubset(set(movement_against["officialLockQualityGate"]["reasons"]))

    agreement_only = _row(62.0, reversalCount=2, tags=["BOOK_AGREEMENT"])
    assert module._is_official(agreement_only) is False

    confirmed = _row(62.0, reversalCount=2, tags=["BOOK_AGREEMENT", "STEAM"])
    assert module._is_official(confirmed) is True, confirmed["officialLockQualityGate"]

    late_conflict = _row(
        63.0,
        temporalFeatures={
            "horizons": {
                "15m": {"velocityPpHr": 0.2, "reversalCount": 0},
                "60m": {"velocityPpHr": -0.1, "reversalCount": 0},
                "180m": {"velocityPpHr": 0.05, "reversalCount": 1},
                "full": {"velocityPpHr": 0.03, "reversalCount": 1},
            }
        },
    )
    assert module._is_official(late_conflict) is False
    assert "late_direction_conflict_without_independent_confirmation" in late_conflict["officialLockQualityGate"]["reasons"]

    print("MLB 60% official lock and independent-confirmation quality gate verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
