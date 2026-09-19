"""Preserve semantic existing-signal use in KS1 forensic development selection.

The market forensic coordinate is an affine recentering of ``market_home_prob``.  The
selected KS1 baseline already contains that raw coordinate, so a tree split on the
recentered alias is not evidence that a new signal was learned.  The original semantic
contract therefore represents a whole family consisting only of such an alias through
its existing raw parent, and only when that parent is genuinely used by the model.

PR #973 changed tree competition by adding real individual-reliever features.  That made
the redundant market alias receive a split in the single-family screen, exposing a latent
fallback-order bug: the selector preferred the alias whenever it happened to split and
only considered the raw equivalent when the alias did not split.  Downstream two-window
selection then required the redundant alias itself to split on both folds.

This development-only adapter restores the stricter semantic rule without changing any
metric, trial, feature source, chronology, holdout, T-10 or serving gate.  All original
single-feature trial scores are retained.  For a whole family that is exactly one affine
recentered coordinate of a raw selected-baseline feature, the alias can never count as a
new learned feature; the raw parent must actually receive a split.  All other families
continue through the existing selector unchanged.

The retained post-#974 evidence also showed that the two-candidate family shortlist can
exclude a third feature that already proved real single-family split use, even while the
bounded joint search contains models that are only one family short of full substantive
use.  This adapter therefore widens that development-only shortlist from two to three
already-proven candidates per genuinely new family.  It adds no feature definition or
LightGBM trial, does not inspect the outer development tail while screening, and leaves
both-fold metric/use gates and the untouched outer-development gate unchanged.
"""
from __future__ import annotations

import ks1.forensic_derived_outer_selection as outer
import ks1.forensic_derived_selected_baseline as base
import ks1.forensic_derived_stable_selection as stable

CONTRACT = "KS1-unified-forensic-derived-selected-baseline-v10"
JOINT_SHORTLIST_PER_GROUP = 3
_ORIGINAL_SCREEN = base.screen_derived_features


def screen_derived_features(fit, baseline_raw, derived, groups):
    """Run the retained screen, then enforce raw-parent semantics for affine aliases."""
    selected, evidence = _ORIGINAL_SCREEN(fit, baseline_raw, derived, groups)
    selected = list(selected)
    equivalent_groups = dict(evidence.get("equivalent_existing_signal_groups") or {})
    baseline_usage = evidence.get("baseline_feature_usage_by_trial") or {}
    baseline_metrics = evidence.get("baseline_by_trial") or {}
    trial_names = tuple(evidence.get("trials") or ())

    if (not trial_names
            or set(baseline_usage) != set(trial_names)
            or set(baseline_metrics) != set(trial_names)):
        raise ValueError("affine semantic screen baseline evidence incomplete")

    for group_name, group_columns in groups.items():
        parent = base._single_affine_existing_parent(group_columns, baseline_raw)
        if parent is None:
            continue

        group_evidence = evidence["groups"].get(group_name)
        if not isinstance(group_evidence, dict):
            raise ValueError("affine semantic screen group evidence missing")

        # A one-parent affine alias of an already-selected raw feature is not a new
        # source of information. Never let the alias itself satisfy family-use proof.
        previous = group_evidence.get("selected")
        if previous in selected:
            selected.remove(previous)
        group_evidence["selected"] = None
        group_evidence["selected_trial"] = None
        equivalent_groups.pop(group_name, None)

        eligible = [
            (
                base._joint_rank_metric(baseline_metrics[trial_name]["brier"]),
                base._joint_rank_metric(baseline_metrics[trial_name]["logloss"]),
                trial_name,
            )
            for trial_name in trial_names
            if parent in set(baseline_usage[trial_name])
        ]
        if eligible:
            _, _, equivalent_trial = min(eligible)
            group_evidence["existing_equivalent_parent"] = parent
            group_evidence["existing_equivalent_parent_trial"] = equivalent_trial
            group_evidence["existing_equivalent_parent_is_new_feature"] = False
            equivalent_groups[group_name] = parent
        else:
            group_evidence["existing_equivalent_parent"] = None
            group_evidence["existing_equivalent_parent_trial"] = None
            group_evidence["existing_equivalent_parent_is_new_feature"] = False

    evidence["method"] = (
        "nested_purged_multitrial_substantive_signal_family_screen_affine_semantic_v2"
    )
    evidence["selected_features"] = selected
    evidence["equivalent_existing_signal_groups"] = equivalent_groups
    evidence["affine_existing_signal_policy"] = (
        "raw_parent_must_be_selected_baseline_input_and_receive_real_split"
    )
    evidence["all_groups_screened"] = all(
        group_evidence.get("selected") is not None
        or group_evidence.get("existing_equivalent_parent") is not None
        for group_evidence in evidence["groups"].values()
    )
    return selected, evidence


def development_select(train):
    """Run the unchanged v8 outer selector with corrected affine-family semantics."""
    current_screen = base.screen_derived_features
    current_base_shortlist = base.JOINT_SHORTLIST_PER_GROUP
    current_stable_shortlist = stable.JOINT_SHORTLIST_PER_GROUP
    if current_screen is not _ORIGINAL_SCREEN:
        raise RuntimeError("forensic base screen unexpectedly replaced")
    if current_base_shortlist != current_stable_shortlist:
        raise RuntimeError("forensic shortlist contract unexpectedly inconsistent")

    # Scope the wider search to this development-only adapter.  The underlying selector
    # still admits only candidates that already received a real split in the single-family
    # screen, and still applies the same two purged windows, frozen trials and metric gates.
    base.screen_derived_features = screen_derived_features
    base.JOINT_SHORTLIST_PER_GROUP = JOINT_SHORTLIST_PER_GROUP
    stable.JOINT_SHORTLIST_PER_GROUP = JOINT_SHORTLIST_PER_GROUP
    try:
        selected, report = outer.development_select(train)
    finally:
        stable.JOINT_SHORTLIST_PER_GROUP = current_stable_shortlist
        base.JOINT_SHORTLIST_PER_GROUP = current_base_shortlist
        base.screen_derived_features = current_screen

    report["contract"] = CONTRACT
    report["affine_existing_signal_semantics"] = {
        "policy": "raw_parent_only_for_whole_single_affine_existing_family",
        "final_holdout_used": False,
        "metric_gates_changed": False,
        "trial_set_changed": False,
    }
    report["bounded_joint_shortlist"] = {
        "per_group": JOINT_SHORTLIST_PER_GROUP,
        "candidate_source": "already_single_family_split_used_only",
        "new_feature_definitions": False,
        "new_hyperparameters": False,
        "outer_development_used_for_shortlisting": False,
        "final_holdout_used_for_shortlisting": False,
    }
    return selected, report
