from __future__ import annotations

import pytest

from hello_world import mlb_supervised_feature_interactions_v2_2 as interactions


def test_interaction_map_uses_only_supplied_pregame_features():
    values = interactions.interaction_map({
        "market_home_centered": 0.10,
        "coverage_min": 0.80,
        "volatility60_sum": 0.20,
        "velocity60_diff": 0.30,
        "velocity180_diff": 0.15,
        "reversal_diff": -0.50,
        "derived_velocity_gap_diff": 0.40,
        "steam_diff": 1.0,
        "derived_book_followthrough_diff": 0.25,
        "team_elo_diff": 0.35,
        "team_recent10_diff": 0.20,
        "team_streak_diff": 0.50,
        "team_rest_diff": 0.40,
    })
    assert values["ix_market_velocity60"] == pytest.approx(0.03)
    assert values["ix_reversal_velocity_gap"] == pytest.approx(-0.20)
    assert values["ix_steam_followthrough"] == pytest.approx(0.25)
    assert values["ix_team_market_disagreement"] == pytest.approx(0.15)
    assert values["ix_streak_rest"] == pytest.approx(0.20)


def test_install_adds_regime_groups_and_is_idempotent():
    class Features:
        VERSION = "base"
        BASE_FEATURES = ("market_home_centered",)
        TEMPORAL_FEATURES = ("velocity60_diff",)
        TEAM_FEATURES = ("team_elo_diff",)
        FUNDAMENTAL_FEATURES = ("fund_starter_quality_diff",)
        V8_FEATURES = ("v8_full_f5_home_gap",)
        FEATURE_GROUPS = {"market": BASE_FEATURES}

        @staticmethod
        def feature_map(record):
            return {
                "market_home_centered": 0.1,
                "velocity60_diff": 0.2,
                "team_elo_diff": 0.3,
            }

    first = interactions.install(Features)
    second = interactions.install(Features)
    assert first is second is Features
    assert "market_temporal_team_regime" in Features.FEATURE_GROUPS
    assert "market_temporal_team_fundamentals_v8_regime" in Features.FEATURE_GROUPS
    values = Features.feature_map({})
    assert "ix_market_velocity60" in values
    assert Features.VERSION.endswith(interactions.VERSION)
