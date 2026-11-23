#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

from mlb_reversal_shape_v1 import analyze


def _h(
    velocity: float,
    reversals: int,
    duration: float,
    pulls: int,
    volatility: float = 0.20,
    coverage: float = 1.0,
    max_gap: float = 15.0,
    acceleration: float = 0.0,
):
    return {
        "velocityPpHr": velocity,
        "accelerationPpHr2": acceleration,
        "reversalCount": reversals,
        "durationMinutes": duration,
        "pullCount": pulls,
        "volatilityPpPerPull": volatility,
        "coverageRatio": coverage,
        "maxGapMinutes": max_gap,
    }


def _signal(tags=(), delta=0.01, probability=0.60, **horizons):
    return {
        "marketConsensusProbability": probability,
        "delta": delta,
        "reversalCount": max((h.get("reversalCount", 0) for h in horizons.values()), default=0),
        "tags": list(tags),
        "temporalFeatures": {"horizons": horizons},
    }


def main() -> int:
    stable = analyze(
        _signal(
            delta=0.015,
            probability=0.62,
            **{
                "15m": _h(1.2, 0, 15, 2, 0.10),
                "60m": _h(0.9, 0, 60, 5, 0.15),
                "180m": _h(0.5, 1, 180, 13, 0.18),
                "full": _h(0.3, 1, 600, 41, 0.20),
            },
        )
    )
    assert stable["persistentTrend"] is True, stable
    assert stable["blocked"] is False, stable
    assert "REVERSAL_SHAPE_PERSISTENT_TREND" in stable["patternTags"]
    assert stable["similaritySignature"].startswith("DIR_UP|REV_LOW|")

    choppy = analyze(
        _signal(
            delta=0.003,
            probability=0.55,
            **{
                "15m": _h(0.2, 1, 15, 2, 0.80),
                "60m": _h(0.1, 3, 60, 5, 1.10),
                "180m": _h(-0.05, 5, 180, 13, 0.90),
                "full": _h(0.02, 10, 600, 41, 0.75),
            },
        )
    )
    assert choppy["lowEfficiencyChurn"] is True, choppy
    assert choppy["highReversalDensity"] is True, choppy
    assert choppy["blocked"] is True, choppy
    assert "low_efficiency_reversal_churn_without_confirmation" in choppy["hardRiskReasons"]
    assert "high_reversal_density_without_confirmation" in choppy["hardRiskReasons"]

    late_shock = analyze(
        _signal(
            delta=0.012,
            probability=0.61,
            **{
                "15m": _h(-4.0, 1, 15, 2, 0.25),
                "60m": _h(0.4, 1, 60, 5, 0.20),
                "180m": _h(0.25, 1, 180, 13, 0.20),
                "full": _h(0.15, 2, 600, 41, 0.20),
            },
        )
    )
    assert late_shock["lateOppositeShock"] is True, late_shock
    assert "late_opposite_shock_without_confirmation" in late_shock["hardRiskReasons"]
    assert "LATE_SHOCK" in late_shock["similaritySignature"]

    confirmed_recovery = analyze(
        _signal(
            tags=("BOOK_AGREEMENT", "STEAM"),
            delta=0.018,
            probability=0.63,
            **{
                "15m": _h(1.4, 0, 15, 2, 0.10),
                "60m": _h(1.0, 1, 60, 5, 0.15),
                "180m": _h(0.7, 2, 180, 13, 0.18),
                "full": _h(0.4, 4, 600, 41, 0.20),
            },
        )
    )
    assert confirmed_recovery["independentConfirmation"] is True
    assert confirmed_recovery["stableConfirmedRecovery"] is True, confirmed_recovery
    assert confirmed_recovery["blocked"] is False, confirmed_recovery
    assert "REVERSAL_SHAPE_CONFIRMED_RECOVERY" in confirmed_recovery["patternTags"]

    weak_history = analyze(
        _signal(
            delta=0.01,
            probability=0.59,
            **{
                "15m": _h(0.4, 0, 15, 2),
                "60m": _h(0.3, 1, 60, 2, coverage=0.40, max_gap=60),
                "180m": _h(0.2, 2, 180, 3, coverage=0.45, max_gap=75),
                "full": _h(0.1, 3, 480, 5, coverage=0.50, max_gap=90),
            },
        )
    )
    assert weak_history["blocked"] is True, weak_history
    assert "temporal_history_unreliable_without_confirmation" in weak_history["hardRiskReasons"]
    assert weak_history["weakCoverageHorizons"] == ["60m", "180m", "full"]

    confirmation_is_strict = analyze(
        _signal(
            tags=("STEAM",),
            delta=0.004,
            probability=0.58,
            **{
                "60m": _h(0.1, 3, 60, 5, 1.0),
                "180m": _h(0.05, 5, 180, 13, 0.9),
                "full": _h(0.02, 9, 600, 41, 0.8),
            },
        )
    )
    assert confirmation_is_strict["steam"] is True
    assert confirmation_is_strict["bookAgreement"] is False
    assert confirmation_is_strict["independentConfirmation"] is False
    assert confirmation_is_strict["blocked"] is True

    print("MLB reversal amplitude, density, persistence, and late-shock analysis verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
