"""Purged development selector for KS1 forensic features over the selected baseline recipe.

The first explicit-derived experiment accidentally trained the challenger on every raw
feature admitted by the data contract, while comparing it with the development-selected
KS1 recipe. When lineup/bullpen raw features already lose to the selected starter recipe,
that comparison confounds the value of the new forensic transforms. This selector keeps
feature admission unchanged, adds the derived features to the exact raw recipe selected
on the same purged development process, and can also screen one representative feature
per forensic family on a second, strictly earlier purged development split. For a derived
coordinate whose raw parents are already in the baseline, the nested screen uses an
information-preserving representation instead of asking an exactly redundant coordinate
to win a LightGBM split beside all of its parents. It never sees or scores the frozen
300-game qualification holdout.
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

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v3"
SCREEN_TRIAL = "shallow"


def selected_baseline_features(report, recipe):
    """Return the exact raw feature recipe chosen without using the final holdout."""
    try:
        features = list(report["trials"][recipe]["features"])
    except (KeyError, TypeError):
        raise ValueError("selected baseline recipe evidence missing") from None
    if not features or len(features) != len(set(features)):
        raise ValueError("selected baseline recipe features invalid")
    return features


def _replacement_plan(baseline_raw, feature):
    """Return a fail-closed, information-preserving coordinate replacement plan."""
    spec = SPECS.get(feature)
    parents = list(spec.get("parents", ())) if spec else []
    operation = spec.get("operation") if spec else None
    replaceable = bool(
        (len(parents) == 1 and operation in ("offset", "offset_minus"))
        or (len(parents) == 2 and operation == "difference")
    )
    replace = bool(parents and replaceable and set(parents).issubset(baseline_raw))
    return {
        "mode": "replace_redundant_parents" if replace else "additive",
        "parents": parents,
        "anchor_parent": parents[0] if replace and len(parents) > 1 else None,
        "operation": operation,
        "information_preserving": True if not replace else replaceable,
    }


def representation_columns(baseline_raw, feature):
    """Build a feature-equivalent coordinate system for one derived candidate.

    If every raw parent is already present, simply appending a deterministic transform
    makes split-use proof depend on LightGBM choosing one of several aliases. Replace the
    redundant parent coordinate(s) only for the current invertible feature operations.
    One-parent affine transforms are invertible. For a two-parent difference, retaining
    the first parent plus the difference preserves both original degrees of freedom.
    Features whose parents are not all in the selected baseline remain additive.
    """
    columns = list(baseline_raw)
    plan = _replacement_plan(set(columns), feature)
    if plan["mode"] == "replace_redundant_parents":
        columns = [column for column in columns if column not in plan["parents"]]
        if plan["anchor_parent"] is not None:
            columns.append(plan["anchor_parent"])
        columns.append(feature)
    elif feature not in columns:
        columns.append(feature)
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("invalid forensic representation columns")
    return columns, plan


def screened_representation_columns(baseline_raw, selected):
    """Apply per-family coordinate replacements without consulting outer labels."""
    columns = list(baseline_raw)
    original = set(baseline_raw)
    replaced_parents = set()
    evidence = {}
    for feature in selected:
        plan = _replacement_plan(original, feature)
        if plan["mode"] == "replace_redundant_parents":
            overlap = replaced_parents.intersection(plan["parents"])
            if overlap:
                raise ValueError("overlapping forensic representation parents: "+",".join(sorted(overlap)))
            columns = [column for column in columns if column not in plan["parents"]]
            if plan["anchor_parent"] is not None and plan["anchor_parent"] not in columns:
                columns.append(plan["anchor_parent"])
            if feature not in columns:
                columns.append(feature)
            replaced_parents.update(plan["parents"])
        elif feature not in columns:
            columns.append(feature)
        evidence[feature] = plan
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("invalid combined forensic representation columns")
    return columns, evidence


def screen_derived_features(fit, baseline_raw, derived, groups):
    """Choose one representative per forensic family on an earlier purged split.

    This is development-only feature selection. The outer development tail remains
    untouched while representatives are chosen, and the frozen qualification holdout is
    not available to this function at all. Deterministic aliases of already-selected raw
    parents are screened in an information-preserving coordinate system so feature-use
    proof measures the transform rather than arbitrary split preference between aliases.
    """
    if SCREEN_TRIAL not in TRIALS:
        raise ValueError("nested screen trial unavailable")
    inner_fit, inner_validation = split_development(fit)
    fit_aug = _augment(inner_fit, derived)
    validation_aug = _augment(inner_validation, derived)
    params = {**PARAMS, **TRIALS[SCREEN_TRIAL]}
    selected = []
    evidence = {
        "method": "nested_purged_representation_aware_one_representative_per_forensic_family",
        "trial": SCREEN_TRIAL,
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
            columns, representation = representation_columns(baseline_raw, feature)
            result = _trial(fit_aug, validation_aug, columns, params)
            used = feature in set(result["features_used_in_splits"])
            candidates[feature] = {
                "brier": result["brier"],
                "logloss": result["logloss"],
                "feature_used_in_splits": used,
                "representation": representation,
            }
            if used:
                usable.append((result["brier"], result["logloss"], feature))
        winner = min(usable)[2] if usable else None
        evidence["groups"][group_name] = {
            "candidates": candidates,
            "selected": winner,
        }
        if winner is not None:
            selected.append(winner)
    evidence["selected_features"] = selected
    evidence["all_groups_screened"] = len(selected) == len(groups)
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
        screened_columns, representation = screened_representation_columns(baseline_raw, screened)
        screen_evidence["combined_representation"] = representation
        screen_evidence["combined_feature_count"] = len(screened_columns)
        evaluate("screened", screened_columns)

    if best is None:
        report["reason"] = "derived_forensic_selected_baseline_candidate_not_superior_on_development"
        return None, report
    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[2]
    report["features"] = best[4]
    return best[3], report
