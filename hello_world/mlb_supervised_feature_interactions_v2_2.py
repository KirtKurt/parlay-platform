"""Leakage-safe nonlinear regime interactions for the MLB V8 shadow model.

The base learner is linear. These deterministic interactions allow it to learn
conditional effects already present at T-minus-45 without introducing future
labels, same-day outcomes, or new production authority.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

VERSION = "MLB-SUPERVISED-FEATURE-INTERACTIONS-v2.2"

INTERACTION_FEATURES = (
    "ix_market_velocity60",
    "ix_market_velocity180",
    "ix_market_acceleration180",
    "ix_market_reversal",
    "ix_market_instability",
    "ix_market_consensus",
    "ix_covered_velocity60",
    "ix_covered_velocity180",
    "ix_reversal_velocity_gap",
    "ix_steam_followthrough",
    "ix_team_market_disagreement",
    "ix_team_recent_market_disagreement",
    "ix_streak_rest",
    "ix_f5_full_game_disagreement",
    "ix_starter_bullpen_late_environment",
)


def _f(values: Mapping[str, Any], name: str) -> float:
    try:
        return float(values.get(name, 0.0) or 0.0)
    except Exception:
        return 0.0


def interaction_map(values: Mapping[str, Any]) -> Dict[str, float]:
    market = _f(values, "market_home_centered")
    coverage = max(0.0, min(1.0, _f(values, "coverage_min")))
    volatility = max(0.0, _f(values, "volatility60_sum"))
    reliability = coverage / (1.0 + volatility)
    team_edge = _f(values, "team_elo_diff")
    recent_edge = _f(values, "team_recent10_diff")
    market_direction = 2.0 * market
    return {
        "ix_market_velocity60": market * _f(values, "velocity60_diff"),
        "ix_market_velocity180": market * _f(values, "velocity180_diff"),
        "ix_market_acceleration180": market * _f(values, "acceleration180_diff"),
        "ix_market_reversal": market * _f(values, "reversal_diff"),
        "ix_market_instability": market * _f(values, "derived_instability_sum"),
        "ix_market_consensus": market * _f(values, "pattern_consensus_diff"),
        "ix_covered_velocity60": reliability * _f(values, "velocity60_diff"),
        "ix_covered_velocity180": reliability * _f(values, "velocity180_diff"),
        "ix_reversal_velocity_gap": _f(values, "reversal_diff") * _f(values, "derived_velocity_gap_diff"),
        "ix_steam_followthrough": _f(values, "steam_diff") * _f(values, "derived_book_followthrough_diff"),
        "ix_team_market_disagreement": team_edge - market_direction,
        "ix_team_recent_market_disagreement": recent_edge - market_direction,
        "ix_streak_rest": _f(values, "team_streak_diff") * _f(values, "team_rest_diff"),
        "ix_f5_full_game_disagreement": _f(values, "v8_full_f5_home_gap") - _f(values, "v8_full_f5_away_gap"),
        "ix_starter_bullpen_late_environment": _f(values, "v8_home_starter_bullpen_spread_divergence") * _f(values, "v8_late_inning_run_environment"),
    }


def install(feature_module: Any) -> Any:
    if getattr(feature_module, "_INQSI_MLB_INTERACTIONS_V2_2_INSTALLED", False):
        return feature_module
    original_feature_map = feature_module.feature_map

    def wrapped_feature_map(record: Mapping[str, Any]) -> Dict[str, float]:
        values = dict(original_feature_map(record))
        values.update(interaction_map(values))
        names = set().union(*feature_module.FEATURE_GROUPS.values())
        return {name: float(values.get(name, 0.0) or 0.0) for name in names}

    base = feature_module.BASE_FEATURES
    temporal = feature_module.TEMPORAL_FEATURES
    team = feature_module.TEAM_FEATURES
    fundamentals = feature_module.FUNDAMENTAL_FEATURES
    v8 = feature_module.V8_FEATURES
    feature_module.FEATURE_GROUPS = dict(feature_module.FEATURE_GROUPS)
    feature_module.FEATURE_GROUPS.update({
        "market_temporal_team_regime": base + temporal + team + INTERACTION_FEATURES,
        "market_temporal_team_fundamentals_regime": base + temporal + team + fundamentals + INTERACTION_FEATURES,
        "market_temporal_team_fundamentals_v8_regime": base + temporal + team + fundamentals + v8 + INTERACTION_FEATURES,
    })
    feature_module.feature_map = wrapped_feature_map
    feature_module.VERSION = f"{feature_module.VERSION}+{VERSION}"
    feature_module._INQSI_MLB_INTERACTIONS_V2_2_INSTALLED = True
    return feature_module
