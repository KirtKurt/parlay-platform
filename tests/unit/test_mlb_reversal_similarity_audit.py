#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
SCRIPT = ROOT / "scripts" / "audit_mlb_reversal_similarities.py"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

spec = importlib.util.spec_from_file_location("audit_mlb_reversal_similarities", SCRIPT)
audit = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(audit)


def _h(velocity, reversals, duration, pulls):
    return {
        "velocityPpHr": velocity,
        "reversalCount": reversals,
        "durationMinutes": duration,
        "pullCount": pulls,
        "coverageRatio": 1.0,
        "maxGapMinutes": 15.0,
        "volatilityPpPerPull": 0.15,
    }


def _row(correct: bool):
    return {
        "correct": correct,
        "predictedSide": "home",
        "homeSignal": {
            "marketConsensusProbability": 0.62,
            "delta": 0.015,
            "reversalCount": 1,
            "tags": ["BOOK_AGREEMENT"],
            "temporalFeatures": {
                "horizons": {
                    "15m": _h(1.2, 0, 15, 2),
                    "60m": _h(0.9, 0, 60, 5),
                    "180m": _h(0.5, 1, 180, 13),
                    "full": _h(0.3, 1, 600, 41),
                }
            },
        },
        "awaySignal": {"marketConsensusProbability": 0.38},
    }


def main() -> int:
    assert audit.wilson(80, 100)["lowPct"] > 70.0
    assert audit.wilson(75, 100)["lowPct"] < 70.0

    rows = [_row(index < 80) for index in range(100)]
    diagnostic = audit.build_report(rows, untouched_test=False)
    assert diagnostic["accuracyPct"] == 80.0
    assert diagnostic["similaritySignatures"][0]["accuracy70Qualified"] is False
    assert "not_declared_untouched_test" in diagnostic["similaritySignatures"][0]["qualificationReasons"]

    untouched = audit.build_report(rows, untouched_test=True)
    cohort = untouched["similaritySignatures"][0]
    assert cohort["gradedCount"] == 100
    assert cohort["accuracy70Qualified"] is True
    assert cohort["wilson95Pct"]["lowPct"] > 70.0
    assert cohort["movementSizePp"]["count"] == 100

    too_small = audit.build_report(rows[:20], untouched_test=True)
    assert too_small["accuracyPct"] == 100.0
    assert too_small["similaritySignatures"][0]["accuracy70Qualified"] is False
    assert "selected_count_below_100" in too_small["similaritySignatures"][0]["qualificationReasons"]

    weak = audit.build_report([_row(index < 75) for index in range(100)], untouched_test=True)
    assert weak["accuracyPct"] == 75.0
    assert weak["similaritySignatures"][0]["accuracy70Qualified"] is False
    assert "wilson_lower_bound_below_70pct" in weak["similaritySignatures"][0]["qualificationReasons"]

    print("MLB similarity cohorts require untouched data, 100 selections, and a Wilson lower bound above 70%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
