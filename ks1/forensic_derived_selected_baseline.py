"""Purged development selector for KS1 forensic features over the selected baseline recipe.

The first explicit-derived experiment accidentally trained the challenger on every raw
feature admitted by the data contract, while comparing it with the development-selected
KS1 recipe. When lineup/bullpen raw features already lose to the selected starter recipe,
that comparison confounds the value of the new forensic transforms. This selector keeps
feature admission unchanged, adds the derived features to the exact raw recipe selected
on the same purged development process, and can also screen one representative feature
per forensic family on a second, strictly earlier purged development split. It never sees
or scores the frozen 300-game qualification holdout.
"""
from __future__ import annotations

from math import isfinite

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

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v3"


def selected_baseline_features(report, recipe):
    """Return the exact raw feature recipe chosen without using the final holdout."""
    try:
        features = list(report["trials"][recipe]["features"])
    except (KeyError, TypeError):
        raise ValueError("selected baseline recipe evidence missing") from None
    if not features or len(features) != len(set(features)):
        raise ValueError("selected baseline recipe features invalid")
    return features


def screen_derived_features(fit, baseline_raw, derived, groups):
    """Choose one representative per forensic family on an earlier purged split.

    This is development-only feature selection. The outer development tail remains
    untouched while representatives are chosen, and the frozen qualification holdout is
    not available to this function at all.
    """
    if not TRIALS:
        raise ValueError("nested screen trials unavailable")
    inner_fit, inner_validation = split_development(fit)
    fit_aug = _augment(inner_fit, derived)
    validation_aug = _augment(inner_validation, derived)
    # A single shallow fit can ignore every feature in an otherwise usable
    # family. Screen the same frozen regularization trials used by the outer
    # selector; do not force splits or add a new parameter search space.
    parameters = {name: {**PARAMS, **updates} for name, updates in TRIALS.items()}
    selected = []
    evidence = {
        "method": "nested_purged_one_representative_per_forensic_family",
        "trials": parameters,
        "inner_fit_games": len(inner_fit),
        "inner_validation_games": len(inner_validation),
        "outer_development_used_for_screening": False,
        "final_holdout_used_for_screening": False,
        "groups": {},
    }
    for group_name, group_columns in groups.items():
        candidates = {}
        usable = []
        for feature in group_columns:
            trials = {}
            eligible = []
            for trial_name, params in parameters.items():
                result = _trial(fit_aug, validation_aug, baseline_raw + [feature], params)
                if not all(isfinite(result[key]) for key in ("brier", "logloss")):
                    raise ValueError("nested screen metrics must be finite")
                used = feature in set(result["features_used_in_splits"])
                trials[trial_name] = {
                    "brier": result["brier"],
                    "logloss": result["logloss"],
                    "feature_used_in_splits": used,
                }
                if used:
                    eligible.append((result["brier"], result["logloss"], trial_name))
            best = min(eligible) if eligible else None
            candidates[feature] = {
                "brier": best[0] if best else None,
                "logloss": best[1] if best else None,
                "feature_used_in_splits": best is not None,
                "selected_trial": best[2] if best else None,
                "trials": trials,
            }
            if best is not None:
                usable.append((best[0], best[1], feature))
        winner = min(usable)[2] if usable else None
        evidence["groups"][group_name] = {
            "candidates": candidates,
            "selected": winner,
        }
        if winner is not None:
            selected.append(winner)
    evidence["selected_features"] = selected
    evidence["all_groups_screened"] = bool(groups) and len(selected) == len(groups)
    return selected, evidence


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
    all_columns = baseline_raw + derived
    best = None

    def evaluate(prefix, columns):
        nonlocal best
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
            result["feature_set"] = prefix or "all_admitted_derived"
            key = f"{prefix}_{trial_name}" if prefix else trial_name
            report["trials"][key] = result
            if result["all_derived_groups_used"] and result["beats_selected_baseline"]:
                candidate = (result["brier"], result["logloss"], key, result, list(columns))
                if best is None or candidate[:3] < best[:3]:
                    best = candidate

    evaluate("", all_columns)

    screened, screen_evidence = screen_derived_features(fit, baseline_raw, derived, groups)
    report["nested_group_screen"] = screen_evidence
    if screen_evidence["all_groups_screened"] and set(screened) != set(derived):
        evaluate("screened", baseline_raw + screened)

    if best is None:
        report["reason"] = "derived_forensic_selected_baseline_candidate_not_superior_on_development"
        return None, report
    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[2]
    report["features"] = best[4]
    return best[3], report
