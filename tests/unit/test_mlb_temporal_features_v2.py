from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_temporal_features_v1 as temporal


def _row(at: str, home: float, books: dict[str, float]) -> dict:
    return {
        "pulled_at": at,
        "game": {"commence_time": "2026-07-23T16:00:00+00:00"},
        "fair": {
            "home": home,
            "away": 1.0 - home,
            "book_probs": {
                book: {"home": probability, "away": 1.0 - probability}
                for book, probability in books.items()
            },
        },
    }


def _series() -> list[dict]:
    return [
        _row("2026-07-23T12:00:00+00:00", 0.48, {"a": 0.47, "b": 0.49, "c": 0.48}),
        _row("2026-07-23T12:15:00+00:00", 0.50, {"a": 0.49, "b": 0.51, "c": 0.50}),
        _row("2026-07-23T12:30:00+00:00", 0.52, {"a": 0.51, "b": 0.53, "c": 0.52}),
        _row("2026-07-23T12:45:00+00:00", 0.51, {"a": 0.50, "b": 0.52, "c": 0.51}),
        _row("2026-07-23T13:00:00+00:00", 0.54, {"a": 0.53, "b": 0.55, "c": 0.54}),
    ]


def test_reversal_size_path_timing_and_market_agreement_are_measured() -> None:
    summary = temporal.summarize_side(_series(), "home", "2026-07-23T13:00:00+00:00")
    full = summary["horizons"]["full"]

    assert summary["version"] == temporal.VERSION
    assert full["reversalCount"] == 2
    assert full["directionalLegCount"] == 3
    assert full["netMovementPp"] == 6.0
    assert full["grossMovementPp"] == 8.0
    assert full["pathEfficiency"] == 0.75
    assert full["latestLeg"]["amplitudePp"] == 3.0
    assert full["previousLeg"]["amplitudePp"] == 1.0
    assert full["latestReversalRecoveryRatio"] == 3.0
    assert full["latestReversalMinutesBeforeEvent"] == 195.0
    assert full["marketFlipCount"] == 1
    assert full["market"]["latestBookCount"] == 3
    assert full["market"]["weightedBookDirectionAgreement"] == 1.0
    assert full["market"]["sharpBookPriorUsed"] is False
    assert 0.0 <= full["signalQuality"]["signalQualityIndex"] <= 100.0
    assert full["signalQuality"]["notWinProbability"] is True


def test_cutoff_excludes_later_points_and_flatten_preserves_new_features() -> None:
    summary = temporal.summarize_side(_series(), "home", "2026-07-23T12:45:00+00:00")
    full = summary["horizons"]["full"]

    assert summary["sourcePointCount"] == 4
    assert summary["excludedAfterCutoffCount"] == 1
    assert full["reversalCount"] == 1
    flattened = temporal.flatten(summary, "home")
    assert flattened["homePathEfficiencyFull"] > 0.0
    assert flattened["homeLatestLegAmplitudePpFull"] == 1.0
    assert "homeWeightedBookDirectionAgreementFull" in flattened
    assert "homeSignalQualityIndexFull" in flattened


def test_provenance_requires_current_version_and_lock_bounded_source() -> None:
    summary = temporal.summarize_side(_series(), "home", "2026-07-23T13:00:00+00:00")
    assert temporal.provenance_is_lock_safe(
        summary,
        "2026-07-23T13:00:00+00:00",
        "2026-07-23T13:15:00+00:00",
    )
    assert not temporal.provenance_is_lock_safe(
        summary,
        "2026-07-23T13:30:00+00:00",
        "2026-07-23T13:15:00+00:00",
    )
