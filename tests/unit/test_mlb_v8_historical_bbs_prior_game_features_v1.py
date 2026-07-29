from __future__ import annotations

from types import SimpleNamespace

from hello_world import mlb_v8_historical_bbs_prior_game_features_v1 as features


def snapshot():
    return {
        "priorCompletedGamesUsed": True,
        "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False,
        "home": {
            "bbsHistoryGames": 20,
            "bbsHistoryCoverage": 20 / 30,
            "bbsWinRate5": 0.8,
            "bbsWinRate10": 0.7,
            "bbsWinRate30": 0.6,
            "bbsRunDiffPerGame5": 2.0,
            "bbsRunDiffPerGame10": 1.5,
            "bbsRunsForPerGame10": 5.5,
            "bbsRunsAgainstPerGame10": 4.0,
            "bbsStreakNormalized": 0.3,
            "bbsRestDaysNormalized": 2 / 7,
            "bbsVenueWinRate10": 0.7,
        },
        "away": {
            "bbsHistoryGames": 18,
            "bbsHistoryCoverage": 18 / 30,
            "bbsWinRate5": 0.4,
            "bbsWinRate10": 0.5,
            "bbsWinRate30": 0.55,
            "bbsRunDiffPerGame5": -1.0,
            "bbsRunDiffPerGame10": -0.5,
            "bbsRunsForPerGame10": 4.0,
            "bbsRunsAgainstPerGame10": 4.5,
            "bbsStreakNormalized": -0.2,
            "bbsRestDaysNormalized": 1 / 7,
            "bbsVenueWinRate10": 0.4,
        },
    }


def test_feature_map_uses_only_verified_prior_game_snapshot():
    value = features.feature_map(
        {
            "slateDateEt": "2026-06-01",
            "frozenFundamentalsSnapshot": snapshot(),
        }
    )

    assert value["bbs_prior_supported"] == 1.0
    assert value["bbs_prior_available"] == 1.0
    assert value["bbs_prior_win_rate5_diff"] == 0.4
    assert value["bbs_prior_run_diff10_diff"] == 0.2
    assert value["bbs_prior_streak_diff"] == 0.5
    assert value["bbs_prior_history_games_diff"] == 2 / 30
    assert value["bbs_prior_history_coverage_min"] == 18 / 30


def test_unverified_snapshot_preserves_only_supported_cohort_marker():
    value = snapshot()
    value["sameDayResultsExcluded"] = False
    result = features.feature_map(
        {
            "slateDateEt": "2026-06-01",
            "frozenFundamentalsSnapshot": value,
        }
    )
    expected = {name: 0.0 for name in features.FEATURES}
    expected["bbs_prior_supported"] = 1.0
    assert result == expected


def test_unsupported_date_cannot_enter_supported_denominator():
    result = features.feature_map(
        {
            "slateDateEt": "2025-07-01",
            "frozenFundamentalsSnapshot": snapshot(),
        }
    )
    assert result["bbs_prior_supported"] == 0.0
    assert result["bbs_prior_available"] == 1.0


def test_install_adds_values_without_mutating_feature_groups_and_is_idempotent():
    module = SimpleNamespace(
        FEATURE_GROUPS={
            "market": ("market",),
            "market_temporal_team_bbs_prior": ("market",) + features.FEATURES,
        },
        VERSION="base",
        feature_map=lambda _record: {"market": 1.0},
    )

    original_groups = dict(module.FEATURE_GROUPS)
    features.install(module)
    first = module.feature_map(
        {
            "slateDateEt": "2026-06-01",
            "frozenFundamentalsSnapshot": snapshot(),
        }
    )
    features.install(module)

    assert module.FEATURE_GROUPS == original_groups
    assert first["market"] == 1.0
    assert first["bbs_prior_available"] == 1.0
    assert first["bbs_prior_supported"] == 1.0
    assert module.VERSION.count(features.VERSION) == 1
