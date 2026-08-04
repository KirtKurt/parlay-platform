"""Complete-slate-aware cadence extension for MLB V7/V9 shadow learning.

The V2 cadence remains authoritative for full refits based on settled-game and
materialized-feature increments. This extension adds a one-complete-slate
trigger for lightweight selective evaluation so a valid daily slate cannot be
ignored merely because it contains fewer than the game-count increment.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

try:
    from scripts import run_mlb_historical_supervised_v9_shadow_cadence as base
except ImportError:  # Direct execution from the scripts directory.
    import run_mlb_historical_supervised_v9_shadow_cadence as base

VERSION = "MLB-V7-LEARNING-CADENCE-STATE-v3-complete-slate-aware"
_BASE_DECIDE = base.decide_cadence
_BASE_REPORT_ANCHORS = base.report_anchor_fields


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _previous_complete_slate_count(previous: Mapping[str, Any]) -> int:
    state = previous.get("state") or {}
    for value in (
        state.get("completeSlateCount"),
        previous.get("completeSlateCount"),
        previous.get("completedSlateCount"),
    ):
        if value not in (None, ""):
            return max(0, _integer(value, 0))
    return 0


def _anchor(
    previous: Mapping[str, Any],
    *,
    key: str,
    performed_key: str,
    previous_count: int,
    fallback_key: str | None = None,
) -> int:
    if previous.get(key) not in (None, ""):
        return max(0, _integer(previous.get(key), previous_count))
    if previous.get(performed_key) is True:
        return previous_count
    if fallback_key and previous.get(fallback_key) not in (None, ""):
        return max(0, _integer(previous.get(fallback_key), previous_count))
    # Migration reports are not proof that a lightweight evaluation happened.
    # Anchor to the report's current slate count so only a genuinely later slate
    # triggers the new cadence path.
    return previous_count


def decide_cadence(
    previous: Mapping[str, Any],
    *,
    current_slate_count: int = 0,
    lightweight_slate_increment: int = 1,
    **kwargs: Any,
) -> Dict[str, Any]:
    result = dict(_BASE_DECIDE(previous, **kwargs))
    previous_slate_count = _previous_complete_slate_count(previous)
    current_slate_count = max(0, _integer(current_slate_count, 0))
    lightweight_slate_increment = max(
        1, _integer(lightweight_slate_increment, 1)
    )

    shadow_anchor = _anchor(
        previous,
        key="lastShadowFitCompleteSlateCount",
        performed_key="shadowRefitPerformed",
        previous_count=previous_slate_count,
    )
    lightweight_anchor = _anchor(
        previous,
        key="lastLightweightEvaluationCompleteSlateCount",
        performed_key="lightweightSelectiveEvaluationPerformed",
        previous_count=previous_slate_count,
        fallback_key="lastShadowFitCompleteSlateCount",
    )
    new_shadow_slates = max(0, current_slate_count - shadow_anchor)
    new_lightweight_slates = max(0, current_slate_count - lightweight_anchor)
    regressed = current_slate_count < shadow_anchor

    refit_reasons = list(result.get("refitReasons") or [])
    lightweight_reasons = list(result.get("lightweightReasons") or [])
    if regressed:
        refit_reasons.append("complete_slate_count_regressed")
        lightweight_reasons.append("full_refit_required")
    if new_lightweight_slates >= lightweight_slate_increment:
        lightweight_reasons.append("complete_slate_increment_reached")

    result.update(
        {
            "version": VERSION,
            "previousReportCompleteSlateCount": previous_slate_count,
            "lastShadowFitCompleteSlateCount": shadow_anchor,
            "lastLightweightEvaluationCompleteSlateCount": lightweight_anchor,
            "newCompleteSlatesSinceLastShadowFit": new_shadow_slates,
            "newCompleteSlatesSinceLastLightweightEvaluation": (
                new_lightweight_slates
            ),
            "remainingCompleteSlatesUntilLightweightEvaluation": max(
                0, lightweight_slate_increment - new_lightweight_slates
            ),
            "lightweightSelectiveEvaluationIncrementCompleteSlates": (
                lightweight_slate_increment
            ),
            "completeSlateCountRegressed": regressed,
            "shouldRefit": bool(refit_reasons),
            "shouldLightweight": bool(lightweight_reasons),
            "refitReasons": sorted(set(refit_reasons)),
            "lightweightReasons": sorted(set(lightweight_reasons)),
        }
    )
    return result


def report_anchor_fields(
    decision: Mapping[str, Any],
    *,
    current_slate_count: int = 0,
    shadow_refit_performed: bool,
    lightweight_performed: bool,
    **kwargs: Any,
) -> Dict[str, Any]:
    result = dict(
        _BASE_REPORT_ANCHORS(
            decision,
            shadow_refit_performed=shadow_refit_performed,
            lightweight_performed=lightweight_performed,
            **kwargs,
        )
    )
    current_slate_count = max(0, _integer(current_slate_count, 0))
    shadow_anchor = (
        current_slate_count
        if shadow_refit_performed
        else max(
            0,
            _integer(
                decision.get("lastShadowFitCompleteSlateCount"),
                current_slate_count,
            ),
        )
    )
    lightweight_anchor = (
        current_slate_count
        if lightweight_performed
        else max(
            0,
            _integer(
                decision.get("lastLightweightEvaluationCompleteSlateCount"),
                shadow_anchor,
            ),
        )
    )
    result.update(
        {
            "v7LearningCadenceStateVersion": VERSION,
            "completeSlateCount": current_slate_count,
            "lastShadowFitCompleteSlateCount": shadow_anchor,
            "lastLightweightEvaluationCompleteSlateCount": lightweight_anchor,
            "previousReportCompleteSlateCount": _integer(
                decision.get("previousReportCompleteSlateCount"), 0
            ),
            "newCompleteSlatesSinceLastShadowFit": _integer(
                decision.get("newCompleteSlatesSinceLastShadowFit"), 0
            ),
            "newCompleteSlatesSinceLastLightweightEvaluation": _integer(
                decision.get(
                    "newCompleteSlatesSinceLastLightweightEvaluation"
                ),
                0,
            ),
            "remainingCompleteSlatesUntilLightweightEvaluation": (
                0
                if lightweight_performed
                else _integer(
                    decision.get(
                        "remainingCompleteSlatesUntilLightweightEvaluation"
                    ),
                    0,
                )
            ),
        }
    )
    return result
