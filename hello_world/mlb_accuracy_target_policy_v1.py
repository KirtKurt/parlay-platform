from __future__ import annotations

import os
from typing import Any, Dict

VERSION = "MLB-ACCURACY-TARGET-POLICY-v3-80pct-rolling-90pct-production-reliability"
ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = 80.0
RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = 90.0
MIN_CLEAN_OFFICIAL = 500
MIN_UNTOUCHED_TEST = 100
MIN_SELECTED_UNTOUCHED_TEST = 100
MIN_EXACT_ODDS_COVERAGE_PCT = 90.0
MAX_RELIABILITY_CALIBRATION_ERROR = 0.10
MIN_ROLLING_24H_SLATE_ACCURACY_PCT = 80.0
RELIABILITY_PROGRESS_MILESTONES_PCT = (50.0, 60.0, 70.0, 80.0)
RUNTIME_SAFETY_VERSION = "MLB-ML-RUNTIME-SAFETY-v6-80pct-rolling-90pct-reliability"
CHAMPION_GATE_VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.6-80pct-rolling-90pct-independent-promotion"


def install() -> Dict[str, Any]:
    """Install separated rolling-audit and production-reliability targets."""
    recommendation = str(RECOMMENDATION_RELIABILITY_THRESHOLD_PCT)
    audit = str(ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT)
    rolling_authority = str(MIN_ROLLING_24H_SLATE_ACCURACY_PCT)

    # Assign rather than setdefault so a stale runtime cannot weaken or restore
    # the explicitly separated targets.
    os.environ["INQSI_MLB_ML_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION"] = str(MIN_CLEAN_OFFICIAL)
    os.environ["INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE"] = str(MIN_EXACT_ODDS_COVERAGE_PCT)
    os.environ["INQSI_MLB_ML_MAX_RELIABILITY_CALIBRATION_ERROR"] = str(MAX_RELIABILITY_CALIBRATION_ERROR)
    os.environ["INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY"] = audit
    os.environ["INQSI_MLB_ROLLING_24H_SLATE_AUTHORITY_TARGET_ACCURACY"] = rolling_authority

    patched = []
    warnings = []
    errors = []

    # These modules are imported earlier in the runtime patch chain. Patch their
    # module-level defaults so reports, winner metadata, and the audit agree on 80%.
    try:
        import mlb_accuracy_target_patch as winner_target

        winner_target.ROLLING_TARGET_ACCURACY_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        patched.append("winner_rolling_target_80pct")
    except Exception as exc:
        warnings.append(f"winner_target:{exc}")

    try:
        import mlb_rolling_24h_audit as rolling_audit

        rolling_audit.TARGET_ACCURACY_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        patched.append("rolling_audit_target_80pct")
    except Exception as exc:
        warnings.append(f"rolling_audit:{exc}")

    try:
        import mlb_real_world_accuracy_semantics_fix as semantics

        semantics.ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        patched.append("real_world_rolling_target_80pct")
    except Exception as exc:
        warnings.append(f"real_world_semantics:{exc}")

    try:
        import mlb_ml_runtime_safety_patch as runtime_safety

        runtime_safety.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.MIN_PRODUCTION_TEST_ROWS = MIN_UNTOUCHED_TEST
        runtime_safety.MIN_PRODUCTION_SELECTED_TEST_ROWS = MIN_SELECTED_UNTOUCHED_TEST
        runtime_safety.MIN_EXACT_ODDS_COVERAGE_PCT = MIN_EXACT_ODDS_COVERAGE_PCT
        runtime_safety.MAX_RELIABILITY_CALIBRATION_ERROR = MAX_RELIABILITY_CALIBRATION_ERROR
        runtime_safety.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        runtime_safety.VERSION = RUNTIME_SAFETY_VERSION
        try:
            import mlb_ml_runtime_overlay as overlay
            overlay.RUNTIME_SAFETY_VERSION = RUNTIME_SAFETY_VERSION
            overlay.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
            overlay.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        except Exception:
            pass
        patched.append("runtime_safety_80pct_rolling_90pct_reliability")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")

    try:
        import mlb_ml_champion_challenger_v1 as champion

        champion.MIN_SELECTED_RELIABILITY_ACCURACY = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.MIN_CLEAN_OFFICIAL = MIN_CLEAN_OFFICIAL
        champion.MIN_UNTOUCHED_TEST = MIN_UNTOUCHED_TEST
        champion.MIN_SELECTED_RELIABILITY_TEST = MIN_SELECTED_UNTOUCHED_TEST
        champion.MIN_SELECTED_PRICE_COVERAGE = MIN_EXACT_ODDS_COVERAGE_PCT
        champion.MAX_RELIABILITY_CALIBRATION_ERROR = MAX_RELIABILITY_CALIBRATION_ERROR
        champion.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        champion.RELIABILITY_PROGRESS_MILESTONES_PCT = RELIABILITY_PROGRESS_MILESTONES_PCT
        champion.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.VERSION = CHAMPION_GATE_VERSION

        if not getattr(champion, "_INQSI_MLB_80PCT_ROLLING_90PCT_PRODUCTION_POLICY_APPLIED", False):
            original_evaluate = champion.evaluate

            def evaluate_with_separated_targets(
                dual_model,
                clean_count,
                playable_evidence_count,
                rolling_slate_accuracy_pct=None,
            ):
                result = original_evaluate(
                    dual_model,
                    clean_count,
                    playable_evidence_count,
                    rolling_slate_accuracy_pct=rolling_slate_accuracy_pct,
                )
                if isinstance(result, dict):
                    result["version"] = CHAMPION_GATE_VERSION
                    result["rolling24hAllGamesAuditTargetPct"] = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
                    result["recommendationReliabilityThresholdPct"] = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
                    result["selectedUntouchedTestPlayabilityAccuracyTargetPct"] = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
                    result["minimumRolling24hSlateAccuracyPct"] = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
                    result["rolling24hSlateAccuracyProgressMilestonesPct"] = list(RELIABILITY_PROGRESS_MILESTONES_PCT)
                    result["rolling24hSlateAccuracyProgressMilestonesReportingOnly"] = True
                    result["policy"] = (
                        "Direction and playability promote automatically and independently only after their applicable "
                        "gates pass. Both require a current rolling 24-hour official-card MLB slate accuracy average of "
                        "80%, 500 clean rows, and 100 total untouched-test rows. Direction separately requires 90% "
                        "untouched outcome accuracy; playability separately requires 100 selected rows, 90% selected "
                        "accuracy, 90% exact locked-odds coverage, and calibration error no greater than 0.10. "
                        "Rolling-slate milestones below 80% are reporting-only."
                    )
                return result

            champion.evaluate = evaluate_with_separated_targets
            champion._INQSI_MLB_80PCT_ROLLING_90PCT_PRODUCTION_POLICY_APPLIED = True

        patched.append("champion_80pct_rolling_90pct_production")
    except Exception as exc:
        errors.append(f"champion:{exc}")

    return {
        "ok": not errors,
        "version": VERSION,
        "rolling24hAllGamesAuditTargetPct": ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT,
        "recommendationReliabilityThresholdPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "selectedUntouchedTestPlayabilityAccuracyTargetPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "minimumCleanOfficial": MIN_CLEAN_OFFICIAL,
        "minimumUntouchedTest": MIN_UNTOUCHED_TEST,
        "minimumSelectedUntouchedTest": MIN_SELECTED_UNTOUCHED_TEST,
        "minimumExactOddsCoveragePct": MIN_EXACT_ODDS_COVERAGE_PCT,
        "maximumReliabilityCalibrationError": MAX_RELIABILITY_CALIBRATION_ERROR,
        "minimumRolling24hSlateAccuracyPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
        "rolling24hSlateAccuracyProgressMilestonesPct": list(RELIABILITY_PROGRESS_MILESTONES_PCT),
        "rolling24hSlateAccuracyProgressMilestonesReportingOnly": True,
        "automaticPromotionAfterApplicableGates": True,
        "patched": patched,
        "warnings": warnings,
        "errors": errors,
        "policy": (
            "Direction and playability require a rolling 24-hour official-card MLB slate accuracy average of at least "
            "80%, then must pass their separate 90% untouched-test reliability gates. They promote automatically and "
            "independently only after their applicable gates pass. Every game still keeps its official pick."
        ),
    }
