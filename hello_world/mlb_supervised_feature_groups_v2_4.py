"""Create coverage-honest MLB V8 feature groups.

Historically, BBD prior-game features were appended to starter/bullpen/lineup
fundamentals groups, and every V8 group also required first-five odds. This module
separates those optional feature families so each candidate is gated only by the
inputs it actually uses.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence, Tuple

try:
    import mlb_v8_historical_bbs_prior_game_features_v1 as bbs_prior
except ImportError:  # package import used by unit tests
    from . import mlb_v8_historical_bbs_prior_game_features_v1 as bbs_prior

VERSION = "MLB-SUPERVISED-FEATURE-GROUPS-v2.4-coverage-honest"

V8_FIRST_FIVE_DEPENDENT = {
    "v8_f5_home_minus_away",
    "v8_f5_available",
    "v8_full_f5_home_gap",
    "v8_full_f5_away_gap",
    "v8_f5_spread_home",
    "v8_f5_spread_away",
    "v8_home_starter_bullpen_spread_divergence",
    "v8_late_inning_run_environment",
}
FIRST_FIVE_INTERACTIONS = {
    "ix_f5_full_game_disagreement",
    "ix_starter_bullpen_late_environment",
}


def _unique(*groups: Iterable[str]) -> Tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            if value not in seen:
                values.append(value)
                seen.add(value)
    return tuple(values)


def _without(values: Sequence[str], excluded: Iterable[str]) -> Tuple[str, ...]:
    blocked = set(excluded)
    return tuple(value for value in values if value not in blocked)


def install(feature_module: Any) -> Any:
    if getattr(feature_module, "_INQSI_MLB_FEATURE_GROUPS_V2_4_INSTALLED", False):
        return feature_module

    groups = dict(feature_module.FEATURE_GROUPS)
    bbs_features = tuple(bbs_prior.FEATURES)

    # Repair the V2.3 conflation if this installer is applied in a warm process.
    for name, values in list(groups.items()):
        if "fundamentals" in name:
            groups[name] = _without(tuple(values), bbs_features)

    team = tuple(groups.get("market_temporal_team") or ())
    regime = tuple(groups.get("market_temporal_team_regime") or team)
    regime_without_f5 = _without(regime, FIRST_FIVE_INTERACTIONS)
    v8 = tuple(getattr(feature_module, "V8_FEATURES", ()))
    v8_fullgame = _without(v8, V8_FIRST_FIVE_DEPENDENT)

    if team:
        groups["market_temporal_team_bbs_prior"] = _unique(team, bbs_features)
        groups["market_temporal_team_v8"] = _unique(team, v8)
        groups["market_temporal_team_v8_fullgame"] = _unique(team, v8_fullgame)
        groups["market_temporal_team_bbs_prior_v8"] = _unique(
            team, bbs_features, v8
        )
        groups["market_temporal_team_bbs_prior_v8_fullgame"] = _unique(
            team, bbs_features, v8_fullgame
        )

    if regime:
        groups["market_temporal_team_bbs_prior_regime"] = _unique(
            regime_without_f5, bbs_features
        )
        groups["market_temporal_team_v8_regime"] = _unique(regime, v8)
        groups["market_temporal_team_v8_fullgame_regime"] = _unique(
            regime_without_f5, v8_fullgame
        )
        groups["market_temporal_team_bbs_prior_v8_regime"] = _unique(
            regime, bbs_features, v8
        )
        groups["market_temporal_team_bbs_prior_v8_fullgame_regime"] = _unique(
            regime_without_f5, bbs_features, v8_fullgame
        )

    feature_module.FEATURE_GROUPS = groups
    feature_module.VERSION = f"{feature_module.VERSION}+{VERSION}"
    feature_module._INQSI_MLB_FEATURE_GROUPS_V2_4_INSTALLED = True
    return feature_module
