"""Deterministic point-in-time forensic feature engineering shared by training and serving.

Every derived value is a pure function of pregame inputs. No labels, incumbent
predictions, final scores, or post-cutoff observations are used. Missing parents remain
missing so LightGBM preserves fail-closed semantics instead of interpreting absence as
zero.

Most parents must pass the ordinary KS1 raw-feature admission contract. The one narrow
exception is the ``individual_bullpen`` family: its parents are reconstructed only inside
the holdout-free historical-development path from exact versioned pre-T10 bullpen rosters
and exact official prior game history. Those parents are explicitly marked development-
only here. They cannot become serving inputs or qualify a model until a later reviewed
live-equivalent adapter is added and the exact selected recipe is frozen.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

CONTRACT = "KS1-forensic-derived-features-v7"
GROUPS = (
    "market", "starter_regime", "starter_workload", "lineup", "bullpen",
    "individual_bullpen",
)
INDIVIDUAL_BULLPEN_PARENT_PREFIXES = (
    "home_individual_bullpen_rank", "away_individual_bullpen_rank",
)
INDIVIDUAL_BULLPEN_WINDOWS = (7, 15, 30)

# Operations are simple pregame transforms over declared parents. Positive pairwise
# advantages are oriented toward the home team where a direction is naturally meaningful.
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
    # Compare each starter's strict-prior short-vs-long movement directly. FIP, ERA,
    # and xwOBA are lower-is-better, so positive values mean the home starter's recent
    # trajectory is better relative to his 30-day baseline than the away starter's.
    "forensic_starter_fip_regime_advantage": {
        "group": "starter_regime", "operation": "difference_of_differences",
        "parents": (
            "away_starter_fip_7d", "away_starter_fip_30d",
            "home_starter_fip_7d", "home_starter_fip_30d",
        ),
    },
    "forensic_starter_era_regime_advantage": {
        "group": "starter_regime", "operation": "difference_of_differences",
        "parents": (
            "away_starter_era_7d", "away_starter_era_30d",
            "home_starter_era_7d", "home_starter_era_30d",
        ),
    },
    "forensic_starter_xwoba_regime_advantage": {
        "group": "starter_regime", "operation": "difference_of_differences",
        "parents": (
            "away_starter_xwoba_7d", "away_starter_xwoba_30d",
            "home_starter_xwoba_7d", "home_starter_xwoba_30d",
        ),
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
    # Aggregate bullpen context. FIP/ERA/xwOBA are lower-is-better, K-BB% and
    # available-count are higher-is-better. Every transform is home-oriented.
    "forensic_bullpen_fip_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_fip_7d", "home_bullpen_context_fip_7d"),
    },
    "forensic_bullpen_era_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_era_7d", "home_bullpen_context_era_7d"),
    },
    "forensic_bullpen_k_bb_pct_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("home_bullpen_context_k_bb_pct_7d", "away_bullpen_context_k_bb_pct_7d"),
    },
    "forensic_bullpen_xwoba_7d_advantage": {
        "group": "bullpen", "operation": "difference",
        "parents": ("away_bullpen_context_xwoba_7d", "home_bullpen_context_xwoba_7d"),
    },
    # Compare each side's short-vs-long pooled bullpen movement directly. Positive
    # means the home bullpen's relative trajectory is better, preserving the same
    # home-advantage orientation as the level features.
    "forensic_bullpen_fip_regime_advantage": {
        "group": "bullpen", "operation": "difference_of_differences",
        "parents": (
            "away_bullpen_context_fip_7d", "away_bullpen_context_fip_30d",
            "home_bullpen_context_fip_7d", "home_bullpen_context_fip_30d",
        ),
    },
    "forensic_bullpen_era_regime_advantage": {
        "group": "bullpen", "operation": "difference_of_differences",
        "parents": (
            "away_bullpen_context_era_7d", "away_bullpen_context_era_30d",
            "home_bullpen_context_era_7d", "home_bullpen_context_era_30d",
        ),
    },
    "forensic_bullpen_k_bb_pct_regime_advantage": {
        "group": "bullpen", "operation": "difference_of_differences",
        "parents": (
            "home_bullpen_context_k_bb_pct_7d", "home_bullpen_context_k_bb_pct_30d",
            "away_bullpen_context_k_bb_pct_7d", "away_bullpen_context_k_bb_pct_30d",
        ),
    },
    "forensic_bullpen_xwoba_regime_advantage": {
        "group": "bullpen", "operation": "difference_of_differences",
        "parents": (
            "away_bullpen_context_xwoba_7d", "away_bullpen_context_xwoba_30d",
            "home_bullpen_context_xwoba_7d", "home_bullpen_context_xwoba_30d",
        ),
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
    # Individual reliever usage ranks are reconstructed only after the final
    # qualification holdout is removed. They deliberately make no leverage-role
    # claim. Lower FIP/ERA and higher K-BB% are oriented as home advantages.
    # The 30-day feature names are retained for backward evidence continuity;
    # 7- and 15-day features add recency without changing rank identity.
    **{
        (
            f"forensic_individual_bullpen_rank{rank}_{metric}_advantage"
            if days == 30 else
            f"forensic_individual_bullpen_rank{rank}_{metric}_{days}d_advantage"
        ): {
            "group": "individual_bullpen",
            "operation": "difference",
            "parents": (
                (f"away_individual_bullpen_rank{rank}_{metric}_{days}d",
                 f"home_individual_bullpen_rank{rank}_{metric}_{days}d")
                if metric in ("fip", "era") else
                (f"home_individual_bullpen_rank{rank}_{metric}_{days}d",
                 f"away_individual_bullpen_rank{rank}_{metric}_{days}d")
            ),
            "development_only_parent": True,
        }
        for rank in (1, 2, 3)
        for metric in ("fip", "era", "k_bb_pct")
        for days in INDIVIDUAL_BULLPEN_WINDOWS
    },
    # The pooled bullpen and starter families already expose short-vs-long regime
    # movement. The exact same strict-prior 7/15/30-day windows are available for
    # each deterministic individual-reliever slot, so expose that movement without
    # changing rank identity or inventing a leverage role. Positive FIP/ERA deltas
    # mean recent deterioration; positive K-BB% deltas mean recent improvement.
    **{
        f"forensic_{side}_individual_bullpen_rank{rank}_{metric}_{days}d_regime_delta": {
            "group": "individual_bullpen",
            "operation": "difference",
            "parents": (
                f"{side}_individual_bullpen_rank{rank}_{metric}_{days}d",
                f"{side}_individual_bullpen_rank{rank}_{metric}_30d",
            ),
            "development_only_parent": True,
        }
        for side in ("home", "away")
        for rank in (1, 2, 3)
        for metric in ("fip", "era", "k_bb_pct")
        for days in (7, 15)
    },
}


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def derive_mapping(values: Mapping[str, object]):
    """Derive one row while preserving missing parents as None."""
    result = {}
    for name, spec in SPECS.items():
        parents = [_number(values.get(parent)) for parent in spec["parents"]]
        if any(value is None for value in parents):
            result[name] = None
            continue
        if spec["operation"] == "difference":
            result[name] = parents[0] - parents[1]
        elif spec["operation"] == "difference_of_differences":
            result[name] = (parents[0] - parents[1]) - (parents[2] - parents[3])
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
        elif spec["operation"] == "difference_of_differences":
            values = (parents[0] - parents[1]) - (parents[2] - parents[3])
        elif spec["operation"] == "offset":
            values = parents[0] + float(spec["offset"])
        elif spec["operation"] == "offset_minus":
            values = float(spec["offset"]) - parents[0]
        else:
            raise ValueError("unknown forensic derivation operation")
        data[name] = values.replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(data, index=frame.index)


def _development_parent_allowed(parent):
    return parent.startswith(INDIVIDUAL_BULLPEN_PARENT_PREFIXES)


def admit(frame: pd.DataFrame, admitted_raw, minimum_nonmissing):
    """Admit derived fields only with proven parents and adequate coverage.

    Ordinary parents must be in ``admitted_raw``. A statically declared development-only
    parent is allowed only for the individual-bullpen namespace and only when the column
    is physically present in the holdout-free frame supplied by the development runner.
    """
    raw = set(admitted_raw)
    derived = derive_frame(frame)
    admitted, rejected = [], {}
    for name, spec in SPECS.items():
        reasons = []
        missing_parents = sorted(set(spec["parents"]) - raw)
        if missing_parents:
            if spec.get("development_only_parent") is True:
                invalid = [parent for parent in missing_parents
                           if parent not in frame.columns or not _development_parent_allowed(parent)]
                if invalid:
                    reasons.append("development_parent_unavailable:" + ",".join(invalid))
            else:
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
                "development_only_parent": spec.get("development_only_parent") is True,
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