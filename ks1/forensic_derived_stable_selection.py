"""Temporally robust purged-development selection for the unified KS1 forensic candidate.

PR #965 proved that one jointly learned five-family candidate could look strongly better on
one earlier 300-game inner validation window and then deteriorate on the untouched outer
development tail. This module keeps the existing v5 source admission, feature definitions,
frozen trials and outer acceptance gate, but makes the interaction-aware inner selector
choose across two consecutive purged development windows instead of one. The frozen
300-game qualification holdout remains unavailable to every function in this module.
"""
from __future__ import annotations

from itertools import product

import ks1.forensic_derived_selected_baseline as base
from ks1.development import TRIALS, select
from ks1.forensic_derived import _augment, _trial
from ks1.forensic_features import CONTRACT as FEATURE_CONTRACT, SPECS, admit as admit_derived, groups as derived_groups
from ks1.retrain_recent import EVALUATION_GAMES, choose_features, split_development
from ks1.train import PARAMS

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v6"
JOINT_SHORTLIST_PER_GROUP = base.JOINT_SHORTLIST_PER_GROUP


def _usage(used, groups, equivalent_groups, selected_by_group):
    usage = {}
    for group_name in groups:
        if group_name in equivalent_groups:
            parent = equivalent_groups[group_name]
            usage[group_name] = [parent] if parent in used else []
        else:
            feature = selected_by_group[group_name]
            usage[group_name] = [feature] if feature in used else []
    return usage


def _baseline_for_fold(fit_aug, validation_aug, baseline_raw, trial_names):
    metrics = {}
    for trial_name in trial_names:
        params = {**PARAMS, **TRIALS[trial_name]}
        result = _trial(fit_aug, validation_aug, baseline_raw, params)
        metrics[trial_name] = {
            "brier": result["brier"],
            "logloss": result["logloss"],
        }
    return metrics


def joint_screen_derived_features(fit, baseline_raw, derived, groups, screen_evidence):
    """Choose a joint family representation that is stable across two purged inner folds.

    Candidate shortlists are still restricted to features that the existing v5 single-
    family screen actually learned in a split. The bounded combinations and the already-
    frozen LightGBM trials are then replayed on two consecutive 300-game validation tails,
    both strictly inside the outer fit population. A combination is eligible only if every
    requested signal family is genuinely split-used on both folds. Ranking minimizes the
    worst same-trial Brier delta first, then worst log-loss delta, then mean deltas. The
    outer development gate remains the final selector and is unchanged.
    """
    if not TRIALS:
        raise ValueError("stable joint screen trials unavailable")
    trial_names = tuple(TRIALS)
    supplied_trials = screen_evidence.get("trials")
    if supplied_trials is not None and tuple(supplied_trials) != trial_names:
        raise ValueError("stable joint screen trial evidence mismatch")
    group_evidence = screen_evidence.get("groups")
    recent_baseline = screen_evidence.get("baseline_by_trial")
    if (supplied_trials is None or not isinstance(group_evidence, dict)
            or not isinstance(recent_baseline, dict)
            or set(recent_baseline) != set(trial_names)):
        return [], {
            "method": "nested_purged_two_window_joint_family_screen_v2",
            "trials": list(trial_names),
            "outer_development_used_for_screening": False,
            "final_holdout_used_for_screening": False,
            "all_groups_screened": False,
            "selected_features": [],
            "selected_trial": None,
            "reason": "stable_joint_screen_evidence_incomplete",
        }

    recent_fit, recent_validation = split_development(fit)
    earlier_fit, earlier_validation = split_development(recent_fit)
    recent_fit_aug = _augment(recent_fit, derived)
    recent_validation_aug = _augment(recent_validation, derived)
    earlier_fit_aug = _augment(earlier_fit, derived)
    earlier_validation_aug = _augment(earlier_validation, derived)
    earlier_baseline = _baseline_for_fold(
        earlier_fit_aug, earlier_validation_aug, baseline_raw, trial_names)

    equivalent_groups = dict(screen_evidence.get("equivalent_existing_signal_groups") or {})
    new_groups = [name for name in groups if name not in equivalent_groups]
    shortlists = {
        name: base._ranked_group_shortlist(group_evidence.get(name, {}))
        for name in new_groups
    }
    evidence = {
        "method": "nested_purged_two_window_joint_family_screen_v2",
        "shortlist_per_group": JOINT_SHORTLIST_PER_GROUP,
        "trials": list(trial_names),
        "folds": {
            "recent": {"fit_games": len(recent_fit), "validation_games": len(recent_validation)},
            "earlier": {"fit_games": len(earlier_fit), "validation_games": len(earlier_validation)},
        },
        "outer_development_used_for_screening": False,
        "final_holdout_used_for_screening": False,
        "equivalent_existing_signal_groups": equivalent_groups,
        "candidate_shortlists": shortlists,
        "combinations": [],
        "selected_features": [],
        "selected_trial": None,
        "all_groups_screened": False,
    }
    missing = [name for name in new_groups if not shortlists.get(name)]
    if missing:
        evidence["reason"] = "stable_joint_family_shortlist_unavailable"
        evidence["missing_groups"] = missing
        return [], evidence

    combinations = list(product(*(shortlists[name] for name in new_groups))) if new_groups else [()]
    best = None
    eligible = 0
    for choice in combinations:
        selected_by_group = dict(zip(new_groups, choice))
        columns = baseline_raw + list(choice)
        for trial_name in trial_names:
            params = {**PARAMS, **TRIALS[trial_name]}
            recent_result = _trial(recent_fit_aug, recent_validation_aug, columns, params)
            earlier_result = _trial(earlier_fit_aug, earlier_validation_aug, columns, params)
            recent_usage = _usage(
                set(recent_result["features_used_in_splits"]), groups,
                equivalent_groups, selected_by_group)
            earlier_usage = _usage(
                set(earlier_result["features_used_in_splits"]), groups,
                equivalent_groups, selected_by_group)
            all_used = all(recent_usage.values()) and all(earlier_usage.values())
            recent_brier_delta = recent_result["brier"] - recent_baseline[trial_name]["brier"]
            recent_logloss_delta = recent_result["logloss"] - recent_baseline[trial_name]["logloss"]
            earlier_brier_delta = earlier_result["brier"] - earlier_baseline[trial_name]["brier"]
            earlier_logloss_delta = earlier_result["logloss"] - earlier_baseline[trial_name]["logloss"]
            item = {
                "features_by_group": selected_by_group,
                "trial": trial_name,
                "folds": {
                    "recent": {
                        "brier": recent_result["brier"],
                        "logloss": recent_result["logloss"],
                        "brier_delta_vs_same_trial_baseline": recent_brier_delta,
                        "logloss_delta_vs_same_trial_baseline": recent_logloss_delta,
                        "signal_group_usage": recent_usage,
                    },
                    "earlier": {
                        "brier": earlier_result["brier"],
                        "logloss": earlier_result["logloss"],
                        "brier_delta_vs_same_trial_baseline": earlier_brier_delta,
                        "logloss_delta_vs_same_trial_baseline": earlier_logloss_delta,
                        "signal_group_usage": earlier_usage,
                    },
                },
                "all_signal_groups_used_on_both_folds": all_used,
            }
            evidence["combinations"].append(item)
            if not all_used:
                continue
            eligible += 1
            briers = (recent_brier_delta, earlier_brier_delta)
            logs = (recent_logloss_delta, earlier_logloss_delta)
            candidate = (
                base._joint_rank_metric(max(briers)),
                base._joint_rank_metric(max(logs)),
                base._joint_rank_metric(sum(briers) / 2.0),
                base._joint_rank_metric(sum(logs) / 2.0),
                tuple(choice), trial_name,
                briers, logs,
            )
            if best is None or candidate[:6] < best[:6]:
                best = candidate

    evidence["combinations_evaluated"] = len(combinations) * len(trial_names)
    evidence["eligible_joint_models"] = eligible
    if best is None:
        evidence["reason"] = "no_joint_model_used_every_signal_family_on_both_inner_folds"
        return [], evidence

    selected = list(best[4])
    evidence["selected_features"] = selected
    evidence["selected_trial"] = best[5]
    evidence["selected_worst_brier_delta_vs_same_trial_baseline"] = max(best[6])
    evidence["selected_worst_logloss_delta_vs_same_trial_baseline"] = max(best[7])
    evidence["selected_mean_brier_delta_vs_same_trial_baseline"] = sum(best[6]) / 2.0
    evidence["selected_mean_logloss_delta_vs_same_trial_baseline"] = sum(best[7]) / 2.0
    evidence["all_groups_screened"] = True
    return selected, evidence


def development_select(train):
    """Run the existing strict outer selector with the two-window joint candidate added."""
    fit, validation = split_development(train)
    raw, omitted, coverage = choose_features(fit)
    derived, rejected, _ = admit_derived(fit, raw, EVALUATION_GAMES)
    groups = derived_groups(derived)
    missing_groups = sorted(name for name, columns in groups.items() if not columns)

    _, baseline_report = select(train)
    baseline_name = baseline_report["selected"]
    baseline_metrics = baseline_report["metrics"][baseline_name]
    baseline_raw = base.selected_baseline_features(baseline_report, baseline_name)
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

    def evaluate(prefix, columns, equivalent_groups=None):
        nonlocal best
        equivalent_groups = dict(equivalent_groups or {})
        for trial_name, updates in TRIALS.items():
            params = {**PARAMS, **updates}
            result = _trial(fit_aug, validation_aug, columns, params)
            used = set(result["features_used_in_splits"])
            result["derived_group_usage"] = {
                name: sorted(used.intersection(group_columns))
                for name, group_columns in groups.items()
            }
            result["existing_equivalent_group_usage"] = {
                name: ([parent] if parent in used else [])
                for name, parent in equivalent_groups.items()
            }
            result["signal_group_usage"] = {
                name: (result["derived_group_usage"][name]
                       or result["existing_equivalent_group_usage"].get(name, []))
                for name in groups
            }
            result["all_derived_groups_used"] = all(result["derived_group_usage"].values())
            result["all_signal_groups_used"] = all(result["signal_group_usage"].values())
            result["beats_selected_baseline"] = bool(
                result["brier"] < baseline_metrics["brier"]
                and result["logloss"] <= baseline_metrics["logloss"])
            result["feature_set"] = prefix or "all_admitted_derived"
            key = f"{prefix}_{trial_name}" if prefix else trial_name
            report["trials"][key] = result
            if result["all_signal_groups_used"] and result["beats_selected_baseline"]:
                candidate = (result["brier"], result["logloss"], key, result, list(columns))
                if best is None or candidate[:3] < best[:3]:
                    best = candidate

    evaluate("", all_columns)

    screened, screen_evidence = base.screen_derived_features(
        fit, baseline_raw, derived, groups)
    report["nested_group_screen"] = screen_evidence
    if screen_evidence["all_groups_screened"] and set(screened) != set(derived):
        evaluate("screened", baseline_raw + screened,
                 screen_evidence.get("equivalent_existing_signal_groups", {}))

    stable_screened, stable_evidence = joint_screen_derived_features(
        fit, baseline_raw, derived, groups, screen_evidence)
    report["nested_joint_group_screen"] = stable_evidence
    if (stable_evidence["all_groups_screened"]
            and set(stable_screened) != set(derived)
            and set(stable_screened) != set(screened)):
        evaluate("joint_screened", baseline_raw + stable_screened,
                 stable_evidence.get("equivalent_existing_signal_groups", {}))

    if best is None:
        report["reason"] = "derived_forensic_selected_baseline_candidate_not_superior_on_development"
        return None, report
    report["accepted_for_final_holdout"] = True
    report["selected_trial"] = best[2]
    report["features"] = best[4]
    return best[3], report
