"""Purged development selector for KS1 forensic features over the selected baseline recipe.

The first explicit-derived experiment accidentally trained the challenger on every raw
feature admitted by the data contract, while comparing it with the development-selected
KS1 recipe.  When lineup/bullpen raw features already lose to the selected starter recipe,
that comparison confounds the value of the new forensic transforms.  This selector keeps
feature admission unchanged but adds the derived features to the exact raw recipe selected
on the same purged development process.  It never sees or scores the frozen 300-game
qualification holdout.
"""
from __future__ import annotations

from ks1.development import TRIALS, select
from ks1.forensic_derived import _augment, _trial
from ks1.forensic_features import (
    CONTRACT as FEATURE_CONTRACT,
    SPECS,
    admit as admit_derived,
    groups as derived_groups,
)
from ks1.retrain_recent import EVALUATION_GAMES, choose_features, split_development
from ks1.train import PARAMS

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v1"


def selected_baseline_features(report, recipe):
    """Return the exact raw feature recipe chosen without using the final holdout."""
    try:
        features = list(report["trials"][recipe]["features"])
    except (KeyError, TypeError):
        raise ValueError("selected baseline recipe evidence missing") from None
    if not features or len(features) != len(set(features)):
        raise ValueError("selected baseline recipe features invalid")
    return features


def development_select(train):
    """Select derived features added to the exact development-selected KS1 recipe."""
    fit, validation = split_development(train)
    raw, omitted, coverage = choose_features(fit)
    derived, rejected, _ = admit_derived(fit, raw, EVALUATION_GAMES)
    groups = derived_groups(derived)
    missing_groups = sorted(name for name, columns in groups.items() if not columns)

    _, baseline_report = select(train)
    baseline_name = baseline_report["selected"]
    baseline_metrics = baseline_report["metrics"][baseline_name]
    baseline_raw = selected_baseline_features(baseline_report, baseline_name)
    if not set(baseline_raw).issubset(raw):
        raise ValueError("selected baseline recipe escaped admitted raw feature contract")

    report = {
        "contract": CONTRACT,
        "feature_contract": FEATURE_CONTRACT,
        "fit_games": len(fit),
        "development_games": len(validation),
        "raw_admitted_features": raw,
        "baseline_raw_features": baseline_raw,
        "derived_admitted_features": derived,
        "derived_rejections": rejected,
        "derived_groups": groups,
        "derived_parent_map": {name: list(SPECS[name]["parents"]) for name in derived},
        "omitted_raw_features": omitted,
        "feature_admission_starter_coverage": coverage,
        "baseline_selected": baseline_name,
        "baseline_metrics": baseline_metrics,
        "baseline_selection": baseline_report,
        "final_holdout_used_for_selection": False,
        "accepted_for_final_holdout": False,
        "trials": {},
        "selected_trial": None,
    }
    if missing_groups:
        report.update(reason="derived_forensic_group_unavailable_on_development",
                      missing_groups=missing_groups)
        return None, report

    fit_aug = _augment(fit, derived)
    validation_aug = _augment(validation, derived)
    columns = baseline_raw + derived
    best = None
    for trial_name, updates in TRIALS.items():
        params = {**PARAMS, **updates}
        result = _trial(fit_aug, validation_aug, columns, params)
        used = set(result["features_used_in_splits"])
        result["derived_group_usage"] = {
            name: sorted(used.intersection(group_columns))
            for name, group_columns in groups.items()
        }
        result["all_derived_groups_used"] = all(result["derived_group_usage"].values())
        result["beats_selected_baseline"] = bool(
            result["brier"] < baseline_metrics["brier"]
            and result["logloss"] <= baseline_metrics["logloss"])
        report["trials"][trial_name] = result
        if result["all_derived_groups_used"] and result["beats_selected_baseline"]:
            if best is None or (result["brier"], result["logloss"], trial_name) < (
                    best[1]["brier"], best[1]["logloss"], best[0]):
                best = (trial_name, result)

    if best is None:
        report["reason"] = "derived_forensic_selected_baseline_candidate_not_superior_on_development"
        return None, report
    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[0]
    report["features"] = columns
    return best[1], report
