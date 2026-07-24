from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hello_world.mlb_historical_daily_optimizer_v1 import (
    Candidate,
    american_to_implied_probability,
    clip_game_snapshots,
    consensus_snapshot,
    devig_two_way,
    extract_game_features,
    predictions_for_candidate,
    snapshot_schedule,
)


def test_american_odds_and_devig_are_two_way_normalized():
    assert american_to_implied_probability(100) == pytest.approx(0.5)
    assert american_to_implied_probability(-200) == pytest.approx(2 / 3)
    home, away, overround = devig_two_way(-110, -110)
    assert home == pytest.approx(0.5)
    assert away == pytest.approx(0.5)
    assert overround > 0


def test_snapshot_schedule_starts_at_1am_et_and_uses_15_minutes():
    times = snapshot_schedule(
        "2025-06-01",
        ["2025-06-01T17:05:00Z", "2025-06-02T00:10:00Z"],
    )
    # June is EDT, so 01:00 America/New_York is 05:00 UTC.
    assert times[0] == datetime(2025, 6, 1, 5, 0, tzinfo=timezone.utc)
    assert all((right - left).total_seconds() == 900 for left, right in zip(times, times[1:]))
    # Latest game locks at 23:25 UTC; final exact cadence slot is 23:15 UTC.
    assert times[-1] == datetime(2025, 6, 1, 23, 15, tzinfo=timezone.utc)


def test_clip_is_per_game_and_excludes_every_observation_after_t_minus_45():
    snapshots = [
        {"observed_at_utc": "2025-06-01T15:00:00Z", "home_probability": 0.50},
        {"observed_at_utc": "2025-06-01T16:15:00Z", "home_probability": 0.52},
        {"observed_at_utc": "2025-06-01T16:30:00Z", "home_probability": 0.70},
    ]
    clipped = clip_game_snapshots(snapshots, "2025-06-01T17:05:00Z")
    assert [row["observed_at_utc"] for row in clipped] == [
        "2025-06-01T15:00:00Z",
        "2025-06-01T16:15:00Z",
    ]


def test_consensus_is_bookmaker_devigged_and_tracks_sharp_divergence():
    event = {
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home", "price": -120},
                            {"name": "Away", "price": 110},
                        ],
                    }
                ],
            },
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home", "price": -130},
                            {"name": "Away", "price": 115},
                        ],
                    }
                ],
            },
        ],
    }
    result = consensus_snapshot(event)
    assert result["book_count"] == 2
    assert result["sharp_book_count"] == 1
    assert 0.5 < result["home_probability"] < 0.7
    assert 0.5 < result["sharp_home_probability"] < 0.7
    assert result["book_dispersion"] > 0


def test_feature_vector_is_built_only_from_the_clipped_path():
    snapshots = [
        {
            "observed_at_utc": "2025-06-01T13:00:00Z",
            "home_probability": 0.48,
            "sharp_home_probability": 0.49,
            "book_dispersion": 0.01,
            "book_count": 8,
            "overround": 0.04,
        },
        {
            "observed_at_utc": "2025-06-01T15:00:00Z",
            "home_probability": 0.50,
            "sharp_home_probability": 0.51,
            "book_dispersion": 0.012,
            "book_count": 10,
            "overround": 0.045,
        },
        {
            "observed_at_utc": "2025-06-01T16:15:00Z",
            "home_probability": 0.53,
            "sharp_home_probability": 0.55,
            "book_dispersion": 0.014,
            "book_count": 11,
            "overround": 0.05,
        },
        # This is after the 16:20 UTC lock and must not affect any feature.
        {
            "observed_at_utc": "2025-06-01T16:30:00Z",
            "home_probability": 0.90,
            "sharp_home_probability": 0.90,
            "book_dispersion": 0.20,
            "book_count": 1,
            "overround": 0.30,
        },
    ]
    features = extract_game_features(snapshots, "2025-06-01T17:05:00Z")
    assert features["open_home_prob"] == pytest.approx(0.48)
    assert features["lock_home_prob"] == pytest.approx(0.53)
    assert features["net_move"] == pytest.approx(0.05)
    assert features["snapshot_count"] == 3
    assert features["book_count"] == pytest.approx((8 + 10 + 11) / 3)
    assert features["sharp_divergence"] == pytest.approx(0.02)


def test_candidate_always_returns_one_winner_pick_per_valid_game():
    candidate = Candidate(
        candidate_id="test",
        feature_names=("lock_home_prob",),
        means=(0.5,),
        scales=(0.1,),
        weights=(0.0,),
        bias=0.0,
        market_blend=1.0,
        l2=0.1,
        validation_min_daily_accuracy=0.0,
        validation_mean_daily_accuracy=0.0,
        validation_pass_day_rate=0.0,
        validation_brier=0.0,
        validation_log_loss=0.0,
    )
    rows = [
        {
            "slate_date": "2025-06-01",
            "game_id": "g1",
            "home_team": "A",
            "away_team": "B",
            "home_win": 1,
            "market_home_probability": 0.60,
            "features": {"lock_home_prob": 0.60},
        },
        {
            "slate_date": "2025-06-01",
            "game_id": "g2",
            "home_team": "C",
            "away_team": "D",
            "home_win": 0,
            "market_home_probability": 0.40,
            "features": {"lock_home_prob": 0.40},
        },
    ]
    predictions, probabilities, labels = predictions_for_candidate(rows, candidate)
    assert [row["pick"] for row in predictions] == ["A", "D"]
    assert probabilities == pytest.approx([0.60, 0.40])
    assert labels == [1, 0]
