"""Persistent cadence anchors for MLB V7/V9 shadow learning.

Hourly evidence publication is not a model fit. Cadence therefore advances only
from actual refits/evaluations and now tracks both newly settled games and newly
materialized point-in-time feature-family rows. A corrected feature corpus may
trigger learning even when the eligible-game count is unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

VERSION = "MLB-V7-LEARNING-CADENCE-STATE-v2-game-and-feature-anchors"


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _legacy_shadow_anchor(
    previous: Mapping[str, Any],
    *,
    previous_count: int,
    previous_fingerprint: str,
) -> tuple[int, str]:
    count = _integer(previous.get("lastShadowFitEligibleGameCount"), -1)
    fingerprint = _text(previous.get("lastShadowFitDatasetFingerprint"))
    if count >= 0 and fingerprint:
        return count, fingerprint
    if previous.get("shadowRefitPerformed") is True:
        return previous_count, previous_fingerprint
    recorded_delta = _integer(previous.get("newEligibleGamesSinceLastShadowFit"), 0)
    legacy_fingerprint = _text(previous.get("previousShadowDatasetFingerprint"))
    if recorded_delta > 0 and legacy_fingerprint:
        return max(0, previous_count - recorded_delta), legacy_fingerprint
    return previous_count, previous_fingerprint


def _legacy_lightweight_anchor(
    previous: Mapping[str, Any],
    *,
    previous_count: int,
    previous_fingerprint: str,
    shadow_count: int,
    shadow_fingerprint: str,
) -> tuple[int, str]:
    count = _integer(previous.get("lastLightweightEvaluationEligibleGameCount"), -1)
    fingerprint = _text(previous.get("lastLightweightEvaluationDatasetFingerprint"))
    if count >= 0 and fingerprint:
        return count, fingerprint
    if previous.get("lightweightSelectiveEvaluationPerformed") is True:
        return previous_count, previous_fingerprint
    return shadow_count, shadow_fingerprint


def _feature_anchor(
    previous: Mapping[str, Any],
    *,
    count_key: str,
    fingerprint_key: str,
    performed_key: str,
    previous_count: int,
    previous_fingerprint: str,
    fallback_count: int,
    fallback_fingerprint: str,
) -> tuple[int, str]:
    count = _integer(previous.get(count_key), -1)
    fingerprint = _text(previous.get(fingerprint_key))
    if count >= 0 and fingerprint:
        return count, fingerprint
    if previous.get(performed_key) is True and previous_fingerprint:
        return previous_count, previous_fingerprint
    if fallback_fingerprint:
        return fallback_count, fallback_fingerprint
    # A report that did not record a feature-fit anchor is not proof of a fit.
    # Force one migration refit instead of anchoring to an arbitrary report.
    return 0, ""


def decide_cadence(
    previous: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    full_increment: int,
    lightweight_increment: int,
    feature_count: int = 0,
    feature_fingerprint: str = "",
    full_feature_increment: int = 50,
    lightweight_feature_increment: int = 10,
    force: bool = False,
) -> Dict[str, Any]:
    previous_state = previous.get("state") or {}
    previous_count = _integer(previous_state.get("eligibleGameCount"), 0)
    previous_fingerprint = _text(previous.get("datasetFingerprint"))
    previous_feature_state = previous.get("featureCorpus") or {}
    previous_feature_count = _integer(
        previous_feature_state.get("materializedFeatureRowCount"), 0
    )
    previous_feature_fingerprint = _text(
        previous_feature_state.get("fingerprint")
        or previous.get("featureCorpusFingerprint")
    )

    current_count = max(0, _integer(current_count, 0))
    fingerprint = _text(fingerprint)
    feature_count = max(0, _integer(feature_count, 0))
    feature_fingerprint = _text(feature_fingerprint)
    full_increment = max(1, _integer(full_increment, 50))
    lightweight_increment = max(1, _integer(lightweight_increment, 25))
    full_feature_increment = max(1, _integer(full_feature_increment, 50))
    lightweight_feature_increment = max(
        1, _integer(lightweight_feature_increment, 10)
    )

    shadow_count, shadow_fingerprint = _legacy_shadow_anchor(
        previous,
        previous_count=previous_count,
        previous_fingerprint=previous_fingerprint,
    )
    lightweight_count, lightweight_fingerprint = _legacy_lightweight_anchor(
        previous,
        previous_count=previous_count,
        previous_fingerprint=previous_fingerprint,
        shadow_count=shadow_count,
        shadow_fingerprint=shadow_fingerprint,
    )
    shadow_feature_count, shadow_feature_fingerprint = _feature_anchor(
        previous,
        count_key="lastShadowFitFeatureRowCount",
        fingerprint_key="lastShadowFitFeatureCorpusFingerprint",
        performed_key="shadowRefitPerformed",
        previous_count=previous_feature_count,
        previous_fingerprint=previous_feature_fingerprint,
        fallback_count=0,
        fallback_fingerprint="",
    )
    lightweight_feature_count, lightweight_feature_fingerprint = _feature_anchor(
        previous,
        count_key="lastLightweightEvaluationFeatureRowCount",
        fingerprint_key="lastLightweightEvaluationFeatureCorpusFingerprint",
        performed_key="lightweightSelectiveEvaluationPerformed",
        previous_count=previous_feature_count,
        previous_fingerprint=previous_feature_fingerprint,
        fallback_count=shadow_feature_count,
        fallback_fingerprint=shadow_feature_fingerprint,
    )

    new_shadow_games = max(0, current_count - shadow_count)
    new_lightweight_games = max(0, current_count - lightweight_count)
    new_shadow_features = max(0, feature_count - shadow_feature_count)
    new_lightweight_features = max(0, feature_count - lightweight_feature_count)
    game_count_regressed = current_count < shadow_count
    feature_count_regressed = feature_count < shadow_feature_count

    shadow_dataset_changed = not shadow_fingerprint or fingerprint != shadow_fingerprint
    lightweight_dataset_changed = (
        not lightweight_fingerprint or fingerprint != lightweight_fingerprint
    )
    shadow_feature_changed = (
        not shadow_feature_fingerprint
        or feature_fingerprint != shadow_feature_fingerprint
    )
    lightweight_feature_changed = (
        not lightweight_feature_fingerprint
        or feature_fingerprint != lightweight_feature_fingerprint
    )
    shadow_dataset_rewritten = bool(
        shadow_dataset_changed and current_count == shadow_count
    )
    lightweight_dataset_rewritten = bool(
        lightweight_dataset_changed and current_count == lightweight_count
    )
    shadow_feature_rewritten = bool(
        shadow_feature_changed and feature_count == shadow_feature_count
    )
    lightweight_feature_rewritten = bool(
        lightweight_feature_changed
        and feature_count == lightweight_feature_count
    )

    refit_reasons = []
    if force:
        refit_reasons.append("forced")
    if not shadow_fingerprint or not shadow_feature_fingerprint:
        refit_reasons.append("missing_refit_anchor")
    if shadow_dataset_changed and new_shadow_games >= full_increment:
        refit_reasons.append("eligible_game_increment_reached")
    if shadow_dataset_rewritten:
        refit_reasons.append("canonical_dataset_rewritten_at_same_count")
    if shadow_feature_changed and new_shadow_features >= full_feature_increment:
        refit_reasons.append("feature_row_increment_reached")
    if shadow_feature_rewritten:
        refit_reasons.append("feature_corpus_rewritten_at_same_count")
    if game_count_regressed or feature_count_regressed:
        refit_reasons.append("training_corpus_regressed")
    should_refit = bool(refit_reasons)

    lightweight_reasons = []
    if should_refit:
        lightweight_reasons.append("full_refit_required")
    if not lightweight_fingerprint or not lightweight_feature_fingerprint:
        lightweight_reasons.append("missing_lightweight_anchor")
    if (
        lightweight_dataset_changed
        and new_lightweight_games >= lightweight_increment
    ):
        lightweight_reasons.append("eligible_game_increment_reached")
    if lightweight_dataset_rewritten:
        lightweight_reasons.append("canonical_dataset_rewritten_at_same_count")
    if (
        lightweight_feature_changed
        and new_lightweight_features >= lightweight_feature_increment
    ):
        lightweight_reasons.append("feature_row_increment_reached")
    if lightweight_feature_rewritten:
        lightweight_reasons.append("feature_corpus_rewritten_at_same_count")
    should_lightweight = bool(lightweight_reasons)

    return {
        "version": VERSION,
        "previousReportEligibleGameCount": previous_count,
        "previousReportDatasetFingerprint": previous_fingerprint,
        "previousReportFeatureRowCount": previous_feature_count,
        "previousReportFeatureCorpusFingerprint": previous_feature_fingerprint,
        "lastShadowFitEligibleGameCount": shadow_count,
        "lastShadowFitDatasetFingerprint": shadow_fingerprint,
        "lastShadowFitFeatureRowCount": shadow_feature_count,
        "lastShadowFitFeatureCorpusFingerprint": shadow_feature_fingerprint,
        "lastLightweightEvaluationEligibleGameCount": lightweight_count,
        "lastLightweightEvaluationDatasetFingerprint": lightweight_fingerprint,
        "lastLightweightEvaluationFeatureRowCount": lightweight_feature_count,
        "lastLightweightEvaluationFeatureCorpusFingerprint": lightweight_feature_fingerprint,
        "newEligibleGamesSinceLastShadowFit": new_shadow_games,
        "newEligibleGamesSinceLastLightweightEvaluation": new_lightweight_games,
        "newFeatureRowsSinceLastShadowFit": new_shadow_features,
        "newFeatureRowsSinceLastLightweightEvaluation": new_lightweight_features,
        "remainingEligibleGamesUntilShadowRefit": max(
            0, full_increment - new_shadow_games
        ),
        "remainingEligibleGamesUntilLightweightEvaluation": max(
            0, lightweight_increment - new_lightweight_games
        ),
        "remainingFeatureRowsUntilShadowRefit": max(
            0, full_feature_increment - new_shadow_features
        ),
        "remainingFeatureRowsUntilLightweightEvaluation": max(
            0, lightweight_feature_increment - new_lightweight_features
        ),
        "shadowDatasetChangedSinceLastFit": shadow_dataset_changed,
        "lightweightDatasetChangedSinceLastEvaluation": lightweight_dataset_changed,
        "shadowFeatureCorpusChangedSinceLastFit": shadow_feature_changed,
        "lightweightFeatureCorpusChangedSinceLastEvaluation": lightweight_feature_changed,
        "eligibleGameCountRegressed": game_count_regressed,
        "featureRowCountRegressed": feature_count_regressed,
        "shadowRefitIncrementGames": full_increment,
        "lightweightSelectiveEvaluationIncrementGames": lightweight_increment,
        "shadowRefitIncrementFeatureRows": full_feature_increment,
        "lightweightSelectiveEvaluationIncrementFeatureRows": lightweight_feature_increment,
        "forceShadowRefit": bool(force),
        "shouldRefit": should_refit,
        "shouldLightweight": should_lightweight,
        "refitReasons": sorted(set(refit_reasons)),
        "lightweightReasons": sorted(set(lightweight_reasons)),
    }


def report_anchor_fields(
    decision: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    feature_count: int = 0,
    feature_fingerprint: str = "",
    shadow_refit_performed: bool,
    lightweight_performed: bool,
) -> Dict[str, Any]:
    current_count = max(0, _integer(current_count, 0))
    fingerprint = _text(fingerprint)
    feature_count = max(0, _integer(feature_count, 0))
    feature_fingerprint = _text(feature_fingerprint)
    shadow_count = (
        current_count
        if shadow_refit_performed
        else _integer(decision.get("lastShadowFitEligibleGameCount"), current_count)
    )
    shadow_fingerprint = (
        fingerprint
        if shadow_refit_performed
        else _text(decision.get("lastShadowFitDatasetFingerprint"))
    )
    shadow_feature_count = (
        feature_count
        if shadow_refit_performed
        else _integer(decision.get("lastShadowFitFeatureRowCount"), feature_count)
    )
    shadow_feature_fingerprint = (
        feature_fingerprint
        if shadow_refit_performed
        else _text(decision.get("lastShadowFitFeatureCorpusFingerprint"))
    )
    lightweight_count = (
        current_count
        if lightweight_performed
        else _integer(
            decision.get("lastLightweightEvaluationEligibleGameCount"), shadow_count
        )
    )
    lightweight_fingerprint = (
        fingerprint
        if lightweight_performed
        else _text(decision.get("lastLightweightEvaluationDatasetFingerprint"))
    )
    lightweight_feature_count = (
        feature_count
        if lightweight_performed
        else _integer(
            decision.get("lastLightweightEvaluationFeatureRowCount"),
            shadow_feature_count,
        )
    )
    lightweight_feature_fingerprint = (
        feature_fingerprint
        if lightweight_performed
        else _text(
            decision.get("lastLightweightEvaluationFeatureCorpusFingerprint")
        )
    )
    return {
        "v7LearningCadenceStateVersion": VERSION,
        "lastShadowFitEligibleGameCount": shadow_count,
        "lastShadowFitDatasetFingerprint": shadow_fingerprint,
        "lastShadowFitFeatureRowCount": shadow_feature_count,
        "lastShadowFitFeatureCorpusFingerprint": shadow_feature_fingerprint,
        "lastLightweightEvaluationEligibleGameCount": lightweight_count,
        "lastLightweightEvaluationDatasetFingerprint": lightweight_fingerprint,
        "lastLightweightEvaluationFeatureRowCount": lightweight_feature_count,
        "lastLightweightEvaluationFeatureCorpusFingerprint": lightweight_feature_fingerprint,
        "previousReportEligibleGameCount": _integer(
            decision.get("previousReportEligibleGameCount"), 0
        ),
        "previousReportDatasetFingerprint": _text(
            decision.get("previousReportDatasetFingerprint")
        ),
        "previousReportFeatureRowCount": _integer(
            decision.get("previousReportFeatureRowCount"), 0
        ),
        "previousReportFeatureCorpusFingerprint": _text(
            decision.get("previousReportFeatureCorpusFingerprint")
        ),
        "newEligibleGamesSinceLastShadowFit": _integer(
            decision.get("newEligibleGamesSinceLastShadowFit"), 0
        ),
        "newEligibleGamesSinceLastLightweightEvaluation": _integer(
            decision.get("newEligibleGamesSinceLastLightweightEvaluation"), 0
        ),
        "newFeatureRowsSinceLastShadowFit": _integer(
            decision.get("newFeatureRowsSinceLastShadowFit"), 0
        ),
        "newFeatureRowsSinceLastLightweightEvaluation": _integer(
            decision.get("newFeatureRowsSinceLastLightweightEvaluation"), 0
        ),
        "remainingEligibleGamesUntilShadowRefit": 0
        if shadow_refit_performed
        else _integer(decision.get("remainingEligibleGamesUntilShadowRefit"), 0),
        "remainingEligibleGamesUntilLightweightEvaluation": 0
        if lightweight_performed
        else _integer(
            decision.get("remainingEligibleGamesUntilLightweightEvaluation"), 0
        ),
        "remainingFeatureRowsUntilShadowRefit": 0
        if shadow_refit_performed
        else _integer(decision.get("remainingFeatureRowsUntilShadowRefit"), 0),
        "remainingFeatureRowsUntilLightweightEvaluation": 0
        if lightweight_performed
        else _integer(
            decision.get("remainingFeatureRowsUntilLightweightEvaluation"), 0
        ),
        "previousShadowDatasetFingerprint": shadow_fingerprint,
        "previousShadowFeatureCorpusFingerprint": shadow_feature_fingerprint,
    }
