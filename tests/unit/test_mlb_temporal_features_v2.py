from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_temporal_features_v1 as temporal


def _row(at: str, home: float) -> dict:
    return {"pulled_at": at, "fair": {"home": home, "away": 1.0 - home}}


def test_market_flip_leg_size_time_and_cutoff_are_lock_safe() -> None:
    series = [
        _row("2026-07-23T10:00:00Z", 0.515),
        _row("2026-07-23T10:15:00Z", 0.510),
        _row("2026-07-23T10:30:00Z", 0.480),
        _row("2026-07-23T10:45:00Z", 0.505),
        _row("2026-07-23T11:00:00Z", 0.400),  # must be excluded
    ]
    summary = temporal.summarize_side(series, "home", "2026-07-23T10:45:00Z")
    full = summary["horizons"]["full"]

    assert summary["excludedAfterCutoffCount"] == 1
    assert summary["sourcePointCount"] == 4
    assert full["reversalCount"] == 1
    assert full["directionalLegCount"] == 2
    assert full["priorLegMovePp"] == 3.5
    assert full["latestLegMovePp"] == 2.5
    assert full["latestLegSignedMovePp"] == 2.5
    assert full["latestLegDurationMinutes"] == 15.0
    assert round(full["reversalRecoveryRatio"], 6) == round(2.5 / 3.5, 6)
    assert full["marketFlipCount"] == 2
    assert full["largestMarketFlipTowardSidePp"] == 2.5
    assert full["marketFlip2To3PpCandidate"] is True
    assert temporal.provenance_is_lock_safe(
        summary,
        "2026-07-23T10:45:00Z",
        "2026-07-23T10:45:00Z",
    ) is True

    flat = temporal.flatten(summary, "home")
    assert flat["homeLargestMarketFlipTowardSidePpFull"] == 2.5
    assert flat["homeMarketFlip2To3PpCandidateFull"] == 1.0


def test_market_flip_candidate_is_side_specific() -> None:
    series = [
        _row("2026-07-23T10:00:00Z", 0.515),
        _row("2026-07-23T10:15:00Z", 0.480),
        _row("2026-07-23T10:30:00Z", 0.505),
    ]
    home = temporal.summarize_side(series, "home", "2026-07-23T10:30:00Z")
    away = temporal.summarize_side(series, "away", "2026-07-23T10:30:00Z")
    assert home["horizons"]["full"]["largestMarketFlipTowardSidePp"] == 2.5
    assert away["horizons"]["full"]["largestMarketFlipAgainstSidePp"] == 2.5
