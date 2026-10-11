#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_temporal_features_v1 as temporal


def _series(probabilities):
    start = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    return [
        {
            "pulled_at": (start + timedelta(minutes=15 * index)).isoformat(),
            "fair": {"home": probability, "away": 1.0 - probability},
        }
        for index, probability in enumerate(probabilities)
    ]


def main() -> int:
    # Changes in percentage points: +1.0, +0.5, -1.2, -0.8, +1.5.
    series = _series([0.50, 0.51, 0.515, 0.503, 0.495, 0.510])
    cutoff = series[-1]["pulled_at"]
    summary = temporal.summarize_side(series, "home", cutoff)
    full = summary["horizons"]["full"]

    assert full["reversalCount"] == 2, full
    assert full["directionalLegCount"] == 3, full
    assert abs(full["netMovePp"] - 1.0) < 1e-6, full
    assert abs(full["grossMovePp"] - 5.0) < 1e-6, full
    assert abs(full["pathEfficiency"] - 0.2) < 1e-6, full
    assert abs(full["maxDirectionalLegMovePp"] - 2.0) < 1e-6, full
    assert abs(full["maxReversalSwingPp"] - 3.5) < 1e-6, full
    assert abs(full["meanReversalSwingPp"] - 3.5) < 1e-6, full
    assert abs(full["latestLegMovePp"] - 1.5) < 1e-6, full
    assert full["latestLegDirection"] == 1, full
    assert full["reversalPathMetricsVersion"] == temporal.REVERSAL_PATH_METRICS_VERSION

    flattened = temporal.flatten(summary, "selected")
    assert flattened["selectedGrossMovePpFull"] == 5.0
    assert flattened["selectedPathEfficiencyFull"] == 0.2
    assert flattened["selectedMaxReversalSwingPpFull"] == 3.5
    assert flattened["selectedLatestLegMovePpFull"] == 1.5

    after_cutoff = list(series) + [
        {
            "pulled_at": (datetime.fromisoformat(cutoff) + timedelta(minutes=15)).isoformat(),
            "fair": {"home": 0.40, "away": 0.60},
        }
    ]
    bounded = temporal.summarize_side(after_cutoff, "home", cutoff)
    assert bounded["excludedAfterCutoffCount"] == 1
    assert bounded["horizons"]["full"]["netMovePp"] == full["netMovePp"]

    print("MLB lock-bounded net, gross, leg, reversal-swing, and path-efficiency metrics verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
