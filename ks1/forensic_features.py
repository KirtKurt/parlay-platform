"""Deterministic point-in-time forensic feature engineering shared by training and serving.

Every derived value is a pure function of already-supported KS1 pregame inputs.  No
labels, incumbent predictions, final scores, or post-cutoff observations are used.
Missing parents remain missing so LightGBM can preserve the existing fail-closed
semantics instead of silently interpreting absence as zero.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

CONTRACT = "KS1-forensic-derived-features-v1"
GROUPS = ("market", "starter_regime", "starter_workload", "lineup", "bullpen")

# operation is either lhs-rhs or offset-parent.  Positive pairwise advantages are
# oriented toward the home team where a direction is naturally meaningful.
SPECS = {
    "forensic_market_home_strength": {
        "group": "market", "operation": "offset", "offset": -0.5,
        "parents": ("market_home_prob",),
    },
    "forensic_home_starter_era_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("home_starter_era_7d", "home_starter_era_30d"),
    },
    "forensic_away_starter_era_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("away_starter_era_7d", "away_starter_era_30d"),
    },
    "forensic_home_starter_fip_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("home_starter_fip_7d", "home_starter_fip_30d"),
    },
    "forensic_away_starter_fip_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("away_starter_fip_7d", "away_starter_fip_30d"),
    },
    "forensic_home_starter_xwoba_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("home_starter_xwoba_7d", "home_starter_xwoba_30d"),
    },
    "forensic_away_starter_xwoba_regime_delta": {
        "group": "starter_regime", "operation": "difference",
        "parents": ("away_starter_xwoba_7d", "away_starter_xwoba_30d"),
    },
    "forensic_home_starter_short_exposure": {
        "group": "starter_workload", "operation": "offset_minus", "offset": 4.5,
        "parents": ("home_pitcher_context_expected_innings",),
    },
    "forensic_away_starter_short_exposure": {
        "group": "starter_workload", "operation": "offset_minus", "offset": 4.5,
        "parents": ("away_pitcher_context_expected_innings",),
    },
    "forensic_starter_expected_innings_advantage": {
        "group": "starter_workload", "operation": "difference",
        "parents": ("home_pitcher_context_expected_innings", "away_pitcher_context_expected_innings"),
    },
    "forensic_lineup_ops_7d_advantage": {
        "group": "lineup", "operation": "difference",
        "parents": ("home_lineup_ops_7d", "away_lineup_ops_7d"),
    },
    "forensic_lineup_xwoba_7d_advantage": {
        "group": "lineup", "operation": "difference",
        "parents": ("home_lineup_xwoba_7d", "away_lineup_xwoba_7d"),
    },
    "forensic_lineup_top4_ops_advantage": {
        "group": "lineup", "operation": "difference",
        "parents": ("home_lineup_top4_ops", "away_lineup_top4_ops"),
    },
    "forensic_home_lineup_ops_regime_delta": {
        "group": "lineup", "operation": "difference",
        "parents": ("home_lineup_ops_7d", "home_lineup_ops_30d"),
    },
    "forensic_away_lineup_ops_regime_delta": {
        "group": "lineup", "operation": "difference",
        "parents": ("away_lineup_ops_7d", "away_lineup_ops_30d"),
    },
    "forensic_home_lineup_xwoba_regime_delta": {
        "group": "lineup", "operation": "difference",
        "parents": ("home_lineup_xwoba_7d", "home_lineup_xwoba_30d"),
    },
    "forensic_away_lineup_xwoba_regime_delta": {
        "group": "lineup", "operation": "difference",
        "parents": ("away_lineup_xwoba_7d", "away_lineup_xwoba_30d"),
    },
    # FIP/ERA are lower-is-better, so away-home is positive when the home bullpen
    # is better.  Available-count is higher-is-better, so home-away is positive.
    "forensic_bullpen_fip_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_fip_7d", "home_bullpen_context_fip_7d"),
    },
    "forensic_bullpen_era_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_era_7d", "home_bullpen_context_era_7d"),
    },
    "forensic_bullpen_available_count_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("home_bullpen_context_available_count", "away_bullpen_context_available_count"),
    },
    "forensic_home_bullpen_fip_regime_delta": {
        "group": "bullpen", "operation": "difference",
        "parents": ("home_bullpen_context_fip_7d", "home_bullpen_context_fip_30d"),
    },
    "forensic_away_bullpen_fip_regime_delta": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_fip_7d", "away_bullpen_context_fip_30d"),
    },
    "forensic_home_bullpen_era_regime_delta": {
        "group": "bullpen", "operation": "difference",
        "parents": ("home_bullpen_context_era_7d", "home_bullpen_context_era_30d"),
    },
    "forensic_away_bullpen_era_regime_delta": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_era_7d", "away_bullpen_context_era_30d"),
    },
}


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def derive_mapping(values: Mapping[str, object]):
    """Derive one serving row while preserving missing parents as None."""
    result = {}
    for name, spec in SPECS.items():
        parents = [_number(values.get(parent)) for parent in spec["parents"]]
        if any(value is None for value in parents):
            result[name] = None
            continue
        if spec["operation"] == "difference":
            result[name] = parents[0] - parents[1]
        elif spec["operation"] == "offset":
            result[name] = parents[0] + float(spec["offset"])
        elif spec["operation"] == "offset_minus":
            result[name] = float(spec["offset"]) - parents[0]
        else:
            raise ValueError("unknown forensic derivation operation")
    return result


def derive_frame(frame: pd.DataFrame):
    """Return derived columns on the same index without mutating the source frame."""
    data = {}
    for name, spec in SPECS.items():
        parents = [pd.to_numeric(frame[parent], errors="coerce")
                   if parent in frame else pd.Series(np.nan, index=frame.index)
                   for parent in spec["parents"]]
        if spec["operation"] == "difference":
            values = parents[0] - parents[1]
        elif spec["operation"] == "offset":
            values = parents[0] + float(spec["offset"])
        elif spec["operation"] == "offset_minus":
            values = float(spec["offset"]) - parents[0]
        else:
            raise ValueError("unknown forensic derivation operation")
        data[name] = values.replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(data, index=frame.index)


def admit(frame: pd.DataFrame, admitted_raw, minimum_nonmissing):
    """Admit derived fields only when every parent passed raw admission and coverage is adequate."""
    raw = set(admitted_raw)
    derived = derive_frame(frame)
    admitted, rejected = [], {}
    for name, spec in SPECS.items():
        reasons = []
        missing_parents = sorted(set(spec["parents"]) - raw)
        if missing_parents:
            reasons.append("parent_not_admitted:" + ",".join(missing_parents))
        values = derived[name]
        nonmissing = int(values.notna().sum())
        distinct = int(values.nunique(dropna=True))
        if nonmissing < minimum_nonmissing:
            reasons.append("below_nonmissing_floor")
        if distinct <= 1:
            reasons.append("unavailable_or_constant")
        if reasons:
            rejected[name] = {
                "group": spec["group"], "parents": list(spec["parents"]),
                "nonmissing": nonmissing, "distinct": distinct, "reasons": reasons,
            }
        else:
            admitted.append(name)
    return admitted, rejected, derived


def groups(features):
    selected = set(features)
    return {group: sorted(name for name, spec in SPECS.items()
                          if spec["group"] == group and name in selected)
            for group in GROUPS}


def raw_parents(features):
    parents = set()
    for name in features:
        spec = SPECS.get(name)
        if spec:
            parents.update(spec["parents"])
    return sorted(parents)
