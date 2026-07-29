from types import SimpleNamespace

from hello_world import mlb_supervised_feature_groups_v2_4 as groups
from hello_world import mlb_v8_historical_bbs_prior_game_features_v1 as bbs


def test_install_separates_bbs_fundamentals_and_optional_first_five():
    module = SimpleNamespace(
        FEATURE_GROUPS={
            "market_temporal_team": ("market", "team"),
            "market_temporal_team_regime": (
                "market",
                "team",
                "ix_f5_full_game_disagreement",
                "ix_team_market_disagreement",
            ),
            "market_temporal_team_fundamentals": (
                "market",
                "team",
                "fund",
                "bbs_prior_available",
            ),
            "market_temporal_team_fundamentals_v8": (
                "market",
                "team",
                "fund",
                "v8_available",
                "v8_f5_available",
            ),
        },
        V8_FEATURES=(
            "v8_available",
            "v8_h2h_home_minus_away",
            "v8_f5_available",
            "v8_f5_home_minus_away",
            "v8_spread_home",
        ),
        VERSION="base",
    )

    groups.install(module)
    groups.install(module)

    assert "bbs_prior_available" not in module.FEATURE_GROUPS[
        "market_temporal_team_fundamentals"
    ]
    assert "fund" not in module.FEATURE_GROUPS["market_temporal_team_bbs_prior"]
    assert "bbs_prior_available" in module.FEATURE_GROUPS[
        "market_temporal_team_bbs_prior"
    ]
    assert "v8_f5_available" in module.FEATURE_GROUPS["market_temporal_team_v8"]
    assert "v8_f5_available" not in module.FEATURE_GROUPS[
        "market_temporal_team_v8_fullgame"
    ]
    assert "ix_f5_full_game_disagreement" not in module.FEATURE_GROUPS[
        "market_temporal_team_v8_fullgame_regime"
    ]
    assert module.VERSION.count(groups.VERSION) == 1
    assert set(bbs.FEATURES).issubset(
        module.FEATURE_GROUPS["market_temporal_team_bbs_prior"]
    )
