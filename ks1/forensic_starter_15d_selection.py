"""Development-only KS1 overlay for strict-prior 15-day starter form.

The ordinary KS1 forensic surface has 7- and 30-day starter regime parents, while the
historical point-in-time source can also reconstruct the explicitly requested 15-day
middle window from official boxes.  This adapter exposes that missing middle-window
representation only inside the already holdout-free development selector.

It does not widen the joint shortlist, add LightGBM trials, change metric/use gates, or
make the reconstructed parents serving inputs.  If a candidate wins development, the
existing serving-adapter stop still applies before the single frozen-300 qualification.
"""
from __future__ import annotations

import ks1.forensic_features as features
from ks1.forensic_derived_affine_semantic_selection import development_select as _development_select

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v11"

DEV_PARENTS = {
    "dev_starter15_home_fip",
    "dev_starter15_away_fip",
    "dev_starter15_home_era",
    "dev_starter15_away_era",
}

STARTER_15D_SPECS = {
    "forensic_home_starter_fip_15d_regime_delta": {
        "group": "starter_regime",
        "operation": "difference",
        "parents": ("dev_starter15_home_fip", "home_starter_fip_30d"),
        "development_only_parent": True,
    },
    "forensic_away_starter_fip_15d_regime_delta": {
        "group": "starter_regime",
        "operation": "difference",
        "parents": ("dev_starter15_away_fip", "away_starter_fip_30d"),
        "development_only_parent": True,
    },
    "forensic_home_starter_era_15d_regime_delta": {
        "group": "starter_regime",
        "operation": "difference",
        "parents": ("dev_starter15_home_era", "home_starter_era_30d"),
        "development_only_parent": True,
    },
    "forensic_away_starter_era_15d_regime_delta": {
        "group": "starter_regime",
        "operation": "difference",
        "parents": ("dev_starter15_away_era", "away_starter_era_30d"),
        "development_only_parent": True,
    },
    "forensic_starter_fip_15d_regime_advantage": {
        "group": "starter_regime",
        "operation": "difference_of_differences",
        "parents": (
            "dev_starter15_away_fip", "away_starter_fip_30d",
            "dev_starter15_home_fip", "home_starter_fip_30d",
        ),
        "development_only_parent": True,
    },
    "forensic_starter_era_15d_regime_advantage": {
        "group": "starter_regime",
        "operation": "difference_of_differences",
        "parents": (
            "dev_starter15_away_era", "away_starter_era_30d",
            "dev_starter15_home_era", "home_starter_era_30d",
        ),
        "development_only_parent": True,
    },
}


def development_select(train):
    """Run the unchanged selector with a scoped 15-day starter development overlay."""
    collisions = sorted(set(STARTER_15D_SPECS).intersection(features.SPECS))
    if collisions:
        raise RuntimeError("starter15 forensic overlay already present: " + ",".join(collisions))

    original_allowed = features._development_parent_allowed

    def development_parent_allowed(parent):
        return parent in DEV_PARENTS or original_allowed(parent)

    features.SPECS.update(STARTER_15D_SPECS)
    features._development_parent_allowed = development_parent_allowed
    try:
        selected, report = _development_select(train)
    finally:
        features._development_parent_allowed = original_allowed
        for name in STARTER_15D_SPECS:
            features.SPECS.pop(name, None)

    report["contract"] = CONTRACT
    report["starter_15d_development_overlay"] = {
        "source": "exact_versioned_pre_t10_identity_plus_strict_prior_official_boxes",
        "window_days": 15,
        "metrics": ["fip", "era"],
        "derived_features": sorted(STARTER_15D_SPECS),
        "development_only_parents": sorted(DEV_PARENTS),
        "statcast_15d_approximated": False,
        "joint_shortlist_changed": False,
        "trial_set_changed": False,
        "metric_gates_changed": False,
        "feature_usage_gate_changed": False,
        "outer_development_gate_changed": False,
        "final_holdout_used": False,
        "serving_schema_changed": False,
    }
    return selected, report
