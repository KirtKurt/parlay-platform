"""Outer-development selection across every independently stable forensic candidate.

The two-window selector proves that a bounded joint feature set is stable on both earlier
purged inner validation windows.  Its current API returns only the top inner-ranked set,
so the untouched outer development tail can reject that set while another already-proven
inner-stable set is never evaluated.  This module preserves that inner proof unchanged and
uses the outer development tail for its intended final development-selection role across
all distinct inner-eligible sets.  The frozen 300-game qualification holdout is not an
argument to this module and remains unavailable here.
"""
from __future__ import annotations

from math import isfinite

import ks1.forensic_derived_stable_selection as stable

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v8"


def _rank_metric(value):
    value = float(value)
    if not isfinite(value):
        raise ValueError("stable candidate metric nonfinite")
    return round(value, 12)


def _eligible_alternatives(report):
    """Return distinct inner-stable feature sets not already outer-evaluated.

    The source evidence was produced without the outer development tail or final holdout.
    This function only deduplicates and deterministically ranks those existing eligible
    sets; it does not manufacture a new feature combination or trial.
    """
    joint = report.get("nested_joint_group_screen") or {}
    groups = report.get("derived_groups") or {}
    equivalent = dict(joint.get("equivalent_existing_signal_groups") or {})

    already_tested = set()
    derived = tuple(report.get("derived_admitted_features") or ())
    if derived:
        already_tested.add(frozenset(derived))
    screened = tuple((report.get("nested_group_screen") or {}).get("selected_features") or ())
    if screened:
        already_tested.add(frozenset(screened))
    primary = tuple(joint.get("selected_features") or ())
    if primary:
        already_tested.add(frozenset(primary))

    best_by_features = {}
    for item in joint.get("combinations") or ():
        if not (item.get("all_signal_groups_used_on_both_folds")
                and item.get("passes_metric_gates_on_both_folds")):
            continue
        by_group = item.get("features_by_group")
        folds = item.get("folds")
        if not isinstance(by_group, dict) or not isinstance(folds, dict):
            raise ValueError("stable candidate evidence malformed")

        features = []
        for group_name, group_columns in groups.items():
            if group_name in equivalent:
                continue
            feature = by_group.get(group_name)
            if feature not in set(group_columns):
                raise ValueError("stable candidate escaped admitted signal family")
            features.append(feature)
        if not features or len(features) != len(set(features)):
            raise ValueError("stable candidate feature set invalid")
        if frozenset(features) in already_tested:
            continue

        recent = folds.get("recent") or {}
        earlier = folds.get("earlier") or {}
        briers = (
            float(recent["brier_delta_vs_same_trial_baseline"]),
            float(earlier["brier_delta_vs_same_trial_baseline"]),
        )
        logs = (
            float(recent["logloss_delta_vs_same_trial_baseline"]),
            float(earlier["logloss_delta_vs_same_trial_baseline"]),
        )
        if not (max(briers) < 0.0 and max(logs) <= 0.0):
            raise ValueError("stable candidate metric gate evidence inconsistent")
        key = tuple(features)
        rank = (
            _rank_metric(max(briers)),
            _rank_metric(max(logs)),
            _rank_metric(sum(briers) / 2.0),
            _rank_metric(sum(logs) / 2.0),
            key,
            str(item.get("trial")),
        )
        candidate = {
            "features": list(features),
            "features_by_group": {name: by_group[name] for name in by_group},
            "inner_trial": item.get("trial"),
            "worst_brier_delta_vs_same_trial_baseline": max(briers),
            "worst_logloss_delta_vs_same_trial_baseline": max(logs),
            "mean_brier_delta_vs_same_trial_baseline": sum(briers) / 2.0,
            "mean_logloss_delta_vs_same_trial_baseline": sum(logs) / 2.0,
            "rank": rank,
        }
        previous = best_by_features.get(key)
        if previous is None or rank < previous["rank"]:
            best_by_features[key] = candidate

    ranked = sorted(best_by_features.values(), key=lambda item: item["rank"])
    for item in ranked:
        item.pop("rank", None)
    return ranked


def development_select(train):
    """Run v7 unchanged, then outer-test any remaining inner-stable sets if needed."""
    selected, report = stable.development_select(train)
    report["contract"] = CONTRACT
    outer = {
        "method": "outer_development_selection_across_inner_stable_joint_candidates_v1",
        "outer_development_used_for_inner_screening": False,
        "final_holdout_used_for_selection": False,
        "candidate_source": "nested_joint_group_screen",
        "candidates": [],
        "selected_trial": None,
    }
    report["outer_stable_candidate_selection"] = outer

    if selected is not None:
        outer["reason"] = "primary_stable_path_already_passed_outer_development"
        return selected, report

    alternatives = _eligible_alternatives(report)
    outer["eligible_alternative_feature_sets"] = len(alternatives)
    if not alternatives:
        outer["reason"] = "no_unevaluated_inner_stable_feature_set"
        return None, report

    fit, validation = stable.split_development(train)
    derived = list(report["derived_admitted_features"])
    groups = dict(report["derived_groups"])
    baseline_raw = list(report["baseline_raw_features"])
    baseline_metrics = dict(report["baseline_metrics"])
    equivalent = dict(
        (report.get("nested_joint_group_screen") or {}).get(
            "equivalent_existing_signal_groups") or {})
    fit_aug = stable._augment(fit, derived)
    validation_aug = stable._augment(validation, derived)

    best = None
    for index, candidate in enumerate(alternatives, 1):
        features = list(candidate["features"])
        columns = baseline_raw + features
        candidate_report = {
            key: value for key, value in candidate.items()
            if key != "features_by_group"
        }
        candidate_report["trials"] = {}
        prefix = f"joint_stable_alternate_{index:02d}"
        for trial_name, updates in stable.TRIALS.items():
            params = {**stable.PARAMS, **updates}
            result = stable._trial(fit_aug, validation_aug, columns, params)
            used = set(result["features_used_in_splits"])
            result["derived_group_usage"] = {
                name: sorted(used.intersection(group_columns))
                for name, group_columns in groups.items()
            }
            result["existing_equivalent_group_usage"] = {
                name: ([parent] if parent in used else [])
                for name, parent in equivalent.items()
            }
            result["signal_group_usage"] = {
                name: (result["derived_group_usage"][name]
                       or result["existing_equivalent_group_usage"].get(name, []))
                for name in groups
            }
            result["all_derived_groups_used"] = all(
                result["derived_group_usage"].values())
            result["all_signal_groups_used"] = all(
                result["signal_group_usage"].values())
            result["beats_selected_baseline"] = bool(
                result["brier"] < baseline_metrics["brier"]
                and result["logloss"] <= baseline_metrics["logloss"])
            result["feature_set"] = prefix
            key = f"{prefix}_{trial_name}"
            report["trials"][key] = result
            candidate_report["trials"][trial_name] = {
                "brier": result["brier"],
                "logloss": result["logloss"],
                "all_signal_groups_used": result["all_signal_groups_used"],
                "beats_selected_baseline": result["beats_selected_baseline"],
            }
            if result["all_signal_groups_used"] and result["beats_selected_baseline"]:
                accepted = (
                    result["brier"], result["logloss"], key, result, list(columns))
                if best is None or accepted[:3] < best[:3]:
                    best = accepted
        outer["candidates"].append(candidate_report)

    if best is None:
        outer["reason"] = "no_inner_stable_alternative_superior_on_outer_development"
        return None, report

    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[2]
    report["features"] = best[4]
    report.pop("reason", None)
    outer["selected_trial"] = best[2]
    outer["reason"] = "inner_stable_alternative_passed_unchanged_outer_development_gate"
    return best[3], report
