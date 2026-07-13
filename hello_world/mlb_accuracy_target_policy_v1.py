from __future__ import annotations

import os
from typing import Any, Dict

VERSION = "MLB-ACCURACY-TARGET-POLICY-v1-90pct-all-games-audit-60pct-recommendation"
ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = 90.0
RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = 60.0
RUNTIME_SAFETY_VERSION = "MLB-ML-RUNTIME-SAFETY-v4-ddb-champion-60pct-recommendation-threshold"
CHAMPION_GATE_VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.4-independent-model-validation-threshold-isolated-promotion-60pct-playability"


def install() -> Dict[str, Any]:
    """Keep the aspirational all-games audit target separate from recommendation gates.

    The 90% figure is reporting-only for the rolling 24-hour audit of every official
    game prediction. Reliability/playability validation uses 60% and must not alter
    or suppress the underlying required winner prediction for any game.
    """
    recommendation = str(RECOMMENDATION_RELIABILITY_THRESHOLD_PCT)
    audit = str(ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT)

    # Assign rather than setdefault so stale Lambda/workflow values cannot retain
    # the temporary 90% recommendation gate.
    os.environ["INQSI_MLB_ML_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY"] = audit

    patched = []
    errors = []

    try:
        import mlb_ml_runtime_safety_patch as runtime_safety

        runtime_safety.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.VERSION = RUNTIME_SAFETY_VERSION
        try:
            import mlb_ml_runtime_overlay as overlay
            overlay.RUNTIME_SAFETY_VERSION = RUNTIME_SAFETY_VERSION
            overlay.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        except Exception:
            pass
        patched.append("runtime_safety_60pct")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")

    try:
        import mlb_ml_champion_challenger_v1 as champion

        champion.MIN_SELECTED_RELIABILITY_ACCURACY = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.VERSION = CHAMPION_GATE_VERSION

        if not getattr(champion, "_INQSI_MLB_60PCT_RECOMMENDATION_POLICY_APPLIED", False):
            original_evaluate = champion.evaluate

            def evaluate_with_separated_targets(dual_model, clean_count, playable_evidence_count):
                result = original_evaluate(dual_model, clean_count, playable_evidence_count)
                if isinstance(result, dict):
                    result["version"] = CHAMPION_GATE_VERSION
                    result["rolling24hAllGamesAuditTargetPct"] = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
                    result["recommendationReliabilityThresholdPct"] = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
                    result["policy"] = (
                        "Direction and playability earn authority independently. Direction must beat the market on "
                        "untouched chronological data. Reliability/recommendation validation uses a 60% selected-sample "
                        "threshold plus calibration, price coverage, and positive ROI. The 90% figure is reporting-only "
                        "for the rolling 24-hour audit of all official games."
                    )
                return result

            champion.evaluate = evaluate_with_separated_targets
            champion._INQSI_MLB_60PCT_RECOMMENDATION_POLICY_APPLIED = True

        patched.append("champion_playability_60pct")
    except Exception as exc:
        errors.append(f"champion:{exc}")

    return {
        "ok": not errors,
        "version": VERSION,
        "rolling24hAllGamesAuditTargetPct": ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT,
        "recommendationReliabilityThresholdPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "patched": patched,
        "errors": errors,
        "policy": (
            "Ninety percent is reporting-only for the rolling 24-hour all-games audit. "
            "Sixty percent is the reliability/recommendation threshold and never removes "
            "the required winner prediction for a game."
        ),
    }
