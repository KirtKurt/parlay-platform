"""Create coverage-honest MLB V8 feature groups.

Retired BBD/BBS prior-game families remain available only behind an explicit
compatibility flag. The autonomous V8 controller disables them, so candidate
selection evaluates official/provider-neutral context and market families only.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Sequence, Tuple

try:
    import mlb_v8_historical_bbs_prior_game_features_v1 as bbs_prior
except ImportError:  # package import used by unit tests
    from . import mlb_v8_historical_bbs_prior_game_features_v1 as bbs_prior

VERSION = "MLB-SUPERVISED-FEATURE-GROUPS-v2.5-provider-neutral-autonomy"

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
TRUE_VALUES = {"1", "true", "yes", "on"}


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


def _retired_bbs_groups_enabled() -> bool:
    return str(
        os.environ.get("MLB_V8_HISTORICAL_BBS_OVERLAY_ENABLED", "true")
    ).strip().lower() in TRUE_VALUES


def install(feature_module: Any) -> Any:
    if getattr(feature_module, "_INQSI_MLB_FEATURE_GROUPS_V2_4_INSTALLED", False):
        return feature_module

    groups = dict(feature_module.FEATURE_GROUPS)
    bbs_features = tuple(bbs_prior.FEATURES)
    include_retired_bbs = _retired_bbs_groups_enabled()

    # Separate optional context families from ordinary fundamentals. In the
    # provider-neutral controller, remove every retired BBS field and group.
    for name, values in list(groups.items()):
        cleaned = _without(tuple(values), bbs_features)
        if not include_retired_bbs and "bbs_prior" in name:
            groups.pop(name, None)
            continue
        if "fundamentals" in name or not include_retired_bbs:
            groups[name] = cleaned

    team = tuple(groups.get("market_temporal_team") or ())
    regime = tuple(groups.get("market_temporal_team_regime") or team)
    regime_without_f5 = _without(regime, FIRST_FIVE_INTERACTIONS)
    v8 = tuple(getattr(feature_module, "V8_FEATURES", ()))
    v8_fullgame = _without(v8, V8_FIRST_FIVE_DEPENDENT)

    if team:
        if include_retired_bbs:
            groups["market_temporal_team_bbs_prior"] = _unique(team, bbs_features)
            groups["market_temporal_team_bbs_prior_v8"] = _unique(
                team, bbs_features, v8
            )
            groups["market_temporal_team_bbs_prior_v8_fullgame"] = _unique(
                team, bbs_features, v8_fullgame
            )
        groups["market_temporal_team_v8"] = _unique(team, v8)
        groups["market_temporal_team_v8_fullgame"] = _unique(team, v8_fullgame)

    if regime:
        if include_retired_bbs:
            groups["market_temporal_team_bbs_prior_regime"] = _unique(
                regime_without_f5, bbs_features
            )
            groups["market_temporal_team_bbs_prior_v8_regime"] = _unique(
                regime, bbs_features, v8
            )
            groups["market_temporal_team_bbs_prior_v8_fullgame_regime"] = _unique(
                regime_without_f5, bbs_features, v8_fullgame
            )
        groups["market_temporal_team_v8_regime"] = _unique(regime, v8)
        groups["market_temporal_team_v8_fullgame_regime"] = _unique(
            regime_without_f5, v8_fullgame
        )

    feature_module.FEATURE_GROUPS = groups
    feature_module.VERSION = f"{feature_module.VERSION}+{VERSION}"
    feature_module._INQSI_MLB_FEATURE_GROUPS_V2_4_INSTALLED = True
    feature_module.RETIRED_BBS_FEATURE_GROUPS_ENABLED = include_retired_bbs
    feature_module.PROVIDER_NEUTRAL_AUTONOMY = not include_retired_bbs
    return feature_module
