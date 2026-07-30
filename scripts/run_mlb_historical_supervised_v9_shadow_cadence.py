"""Persistent cadence anchors for MLB V7/V9 shadow learning.

A scheduled report is not a model fit. Cadence is measured from the last actual
refit and lightweight evaluation. A material feature-only dataset change at the
same eligible-game count is also a reason to evaluate immediately; otherwise
point-in-time feature backfills can never reach the learner until 50 more games
arrive.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

VERSION = "MLB-V7-LEARNING-CADENCE-STATE-v2-feature-aware-refit-anchors"


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

    # Older reports recorded the accumulated delta and the prior fitted
    # fingerprint but did not yet have explicit anchors. Recover that state when
    # available instead of silently resetting the counter to the current report.
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


def decide_cadence(
    previous: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    full_increment: int,
    lightweight_increment: int,
    force: bool = False,
) -> Dict[str, Any]:
    previous_state = previous.get("state") or {}
    previous_count = _integer(previous_state.get("eligibleGameCount"), 0)
    previous_fingerprint = _text(previous.get("datasetFingerprint"))
    current_count = max(0, _integer(current_count, 0))
    fingerprint = _text(fingerprint)
    full_increment = max(1, _integer(full_increment, 50))
    lightweight_increment = max(1, _integer(lightweight_increment, 25))

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

    new_shadow_games = max(0, current_count - shadow_count)
    new_lightweight_games = max(0, current_count - lightweight_count)
    shadow_dataset_changed = not shadow_fingerprint or fingerprint != shadow_fingerprint
    lightweight_dataset_changed = (
        not lightweight_fingerprint or fingerprint != lightweight_fingerprint
    )

    # The feature-aware fingerprint includes immutable target-context and market-
    # expansion overlays. If that material changes without a game-count change,
    # waiting for another 50 games would discard usable information indefinitely.
    shadow_feature_only_change = bool(
        shadow_fingerprint
        and shadow_dataset_changed
        and current_count == shadow_count
    )
    lightweight_feature_only_change = bool(
        lightweight_fingerprint
        and lightweight_dataset_changed
        and current_count == lightweight_count
    )

    should_refit = bool(
        force
        or not shadow_fingerprint
        or shadow_feature_only_change
        or (shadow_dataset_changed and new_shadow_games >= full_increment)
    )
    should_lightweight = bool(
        should_refit
        or force
        or not lightweight_fingerprint
        or lightweight_feature_only_change
        or (
            lightweight_dataset_changed
            and new_lightweight_games >= lightweight_increment
        )
    )

    return {
        "version": VERSION,
        "previousReportEligibleGameCount": previous_count,
        "previousReportDatasetFingerprint": previous_fingerprint,
        "lastShadowFitEligibleGameCount": shadow_count,
        "lastShadowFitDatasetFingerprint": shadow_fingerprint,
        "lastLightweightEvaluationEligibleGameCount": lightweight_count,
        "lastLightweightEvaluationDatasetFingerprint": lightweight_fingerprint,
        "newEligibleGamesSinceLastShadowFit": new_shadow_games,
        "newEligibleGamesSinceLastLightweightEvaluation": new_lightweight_games,
        "remainingEligibleGamesUntilShadowRefit": max(
            0, full_increment - new_shadow_games
        ),
        "remainingEligibleGamesUntilLightweightEvaluation": max(
            0, lightweight_increment - new_lightweight_games
        ),
        "shadowDatasetChangedSinceLastFit": shadow_dataset_changed,
        "lightweightDatasetChangedSinceLastEvaluation": lightweight_dataset_changed,
        "featureOnlyDatasetChangeSinceLastShadowFit": shadow_feature_only_change,
        "featureOnlyDatasetChangeSinceLastLightweightEvaluation": (
            lightweight_feature_only_change
        ),
        "shadowRefitIncrementGames": full_increment,
        "lightweightSelectiveEvaluationIncrementGames": lightweight_increment,
        "forceShadowRefit": bool(force),
        "shouldRefit": should_refit,
        "shouldLightweight": should_lightweight,
    }


def report_anchor_fields(
    decision: Mapping[str, Any],
    *,
    current_count: int,
    fingerprint: str,
    shadow_refit_performed: bool,
    lightweight_performed: bool,
) -> Dict[str, Any]:
    current_count = max(0, _integer(current_count, 0))
    fingerprint = _text(fingerprint)
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
    lightweight_count = (
        current_count
        if lightweight_performed
        else _integer(
            decision.get("lastLightweightEvaluationEligibleGameCount"),
            shadow_count,
        )
    )
    lightweight_fingerprint = (
        fingerprint
        if lightweight_performed
        else _text(decision.get("lastLightweightEvaluationDatasetFingerprint"))
    )
    return {
        "v7LearningCadenceStateVersion": VERSION,
        "lastShadowFitEligibleGameCount": shadow_count,
        "lastShadowFitDatasetFingerprint": shadow_fingerprint,
        "lastLightweightEvaluationEligibleGameCount": lightweight_count,
        "lastLightweightEvaluationDatasetFingerprint": lightweight_fingerprint,
        "previousReportEligibleGameCount": _integer(
            decision.get("previousReportEligibleGameCount"), 0
        ),
        "previousReportDatasetFingerprint": _text(
            decision.get("previousReportDatasetFingerprint")
        ),
        "newEligibleGamesSinceLastShadowFit": _integer(
            decision.get("newEligibleGamesSinceLastShadowFit"), 0
        ),
        "newEligibleGamesSinceLastLightweightEvaluation": _integer(
            decision.get("newEligibleGamesSinceLastLightweightEvaluation"), 0
        ),
        "remainingEligibleGamesUntilShadowRefit": 0
        if shadow_refit_performed
        else _integer(decision.get("remainingEligibleGamesUntilShadowRefit"), 0),
        "remainingEligibleGamesUntilLightweightEvaluation": 0
        if lightweight_performed
        else _integer(
            decision.get("remainingEligibleGamesUntilLightweightEvaluation"), 0
        ),
        "featureOnlyDatasetChangeSinceLastShadowFit": bool(
            decision.get("featureOnlyDatasetChangeSinceLastShadowFit")
        ),
        "featureOnlyDatasetChangeSinceLastLightweightEvaluation": bool(
            decision.get(
                "featureOnlyDatasetChangeSinceLastLightweightEvaluation"
            )
        ),
        "previousShadowDatasetFingerprint": shadow_fingerprint,
    }
