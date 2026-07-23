from __future__ import annotations

import os
from typing import Any, Dict


VERSION = "MLB-ACCURACY-TARGET-POLICY-v5-70pct-evidence-admission-manual-first"
ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = 90.0
RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = 90.0
MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT = 90.0
MIN_CLEAN_OFFICIAL = 500
MIN_UNTOUCHED_TEST = 100
MIN_SELECTED_UNTOUCHED_TEST = 100
MIN_EXACT_ODDS_COVERAGE_PCT = 90.0
MAX_RELIABILITY_CALIBRATION_ERROR = 0.10
MIN_ROLLING_24H_SLATE_ACCURACY_PCT = 90.0
INDIVIDUAL_GAME_OFFICIAL_PICK_PROBABILITY_FLOOR_PCT = 60.0
MIN_RECOMMENDATION_EVIDENCE_PRECISION_PCT = 70.0
RELIABILITY_PROGRESS_MILESTONES_PCT = (50.0, 60.0, 70.0, 80.0)
RUNTIME_SAFETY_VERSION = "MLB-ML-RUNTIME-SAFETY-v5-90pct-exact-odds-calibrated"
CHAMPION_GATE_VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.6-retired-shadow-only"


def install() -> Dict[str, Any]:
    """Install reporting targets and the evidence-backed recommendation admission rule."""
    recommendation = str(RECOMMENDATION_RELIABILITY_THRESHOLD_PCT)
    audit = str(ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT)

    os.environ["INQSI_MLB_ML_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY"] = recommendation
    os.environ["INQSI_MLB_ML_MIN_OUTCOME_UNTOUCHED_ACCURACY"] = str(MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT)
    os.environ["INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION"] = str(MIN_CLEAN_OFFICIAL)
    os.environ["INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE"] = str(MIN_EXACT_ODDS_COVERAGE_PCT)
    os.environ["INQSI_MLB_ML_MAX_RELIABILITY_CALIBRATION_ERROR"] = str(MAX_RELIABILITY_CALIBRATION_ERROR)
    os.environ["INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY"] = audit
    os.environ["INQSI_MLB_ROLLING_24H_SLATE_AUTHORITY_TARGET_ACCURACY"] = str(
        MIN_ROLLING_24H_SLATE_ACCURACY_PCT
    )
    os.environ["INQSI_MLB_INDIVIDUAL_GAME_OFFICIAL_PICK_PROBABILITY_FLOOR_PCT"] = str(
        INDIVIDUAL_GAME_OFFICIAL_PICK_PROBABILITY_FLOOR_PCT
    )
    os.environ["INQSI_MLB_MIN_RECOMMENDATION_EVIDENCE_PRECISION_PCT"] = str(
        MIN_RECOMMENDATION_EVIDENCE_PRECISION_PCT
    )
    # The quality gate defaults to compatibility mode when imported in isolation.
    # The production runtime explicitly enables the stronger admission rule here.
    os.environ["INQSI_MLB_ENFORCE_70_PRECISION_ADMISSION"] = "true"

    patched = []
    errors = []

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
            overlay.MIN_EXACT_ODDS_COVERAGE_PCT = MIN_EXACT_ODDS_COVERAGE_PCT
            overlay.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        except Exception:
            pass
        patched.append("runtime_safety_90pct_exact_odds_calibration")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")

    try:
        import mlb_ml_champion_challenger_v1 as champion

        champion.MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT = MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT
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
        champion.AUTO_PROMOTE = False
        champion.AUTOMATIC_PROMOTION_SUPPORTED = False
        champion.LEGACY_V1_AUTHORITY_RETIRED = True
        patched.append("legacy_v1_champion_shadow_only")
    except Exception as exc:
        errors.append(f"champion:{exc}")

    precision_status: Dict[str, Any] = {}
    try:
        import mlb_official_lock_quality_gate as official_gate
        import mlb_precision_admission_gate_v1 as precision_gate
        import mlb_real_world_accuracy_patch as accuracy
        import mlb_signal_validation_registry_v1 as validation_registry

        official_gate.apply(accuracy)
        precision_status = validation_registry.status()
        patched.append("official_lock_60pct_direction_70pct_evidence_admission")
        patched.append(f"precision_gate:{precision_gate.VERSION}")
    except Exception as exc:
        errors.append(f"official_lock_quality:{exc}")

    return {
        "ok": not errors,
        "version": VERSION,
        "rolling24hAllGamesAuditTargetPct": ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT,
        "recommendationReliabilityThresholdPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "minimumOutcomeUntouchedAccuracyPct": MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT,
        "selectedUntouchedTestPlayabilityAccuracyTargetPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "minimumCleanOfficial": MIN_CLEAN_OFFICIAL,
        "minimumUntouchedTest": MIN_UNTOUCHED_TEST,
        "minimumSelectedUntouchedTest": MIN_SELECTED_UNTOUCHED_TEST,
        "minimumExactOddsCoveragePct": MIN_EXACT_ODDS_COVERAGE_PCT,
        "maximumReliabilityCalibrationError": MAX_RELIABILITY_CALIBRATION_ERROR,
        "minimumRolling24hSlateAccuracyPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
        "minimumRecommendationEvidencePrecisionPct": MIN_RECOMMENDATION_EVIDENCE_PRECISION_PCT,
        "precisionAdmissionEnforced": True,
        "precisionValidationRegistry": precision_status,
        "rolling24hSlateAccuracyProgressMilestonesPct": list(RELIABILITY_PROGRESS_MILESTONES_PCT),
        "rolling24hSlateAccuracyProgressMilestonesReportingOnly": True,
        "rolling24hAccuracyAffectsPromotion": False,
        "automaticPromotionAfterApplicableGates": False,
        "firstPromotionRequiresManualReview": True,
        "legacyV1AuthorityEnabled": False,
        "v2AwsNativeShadowTraining": True,
        "roiPromotionGateRequired": False,
        "everyGameRetainsOfficialPick": True,
        "everyGameRetainsVisibleLockedPrediction": True,
        "playabilitySeparateFromOfficialPick": True,
        "individualGameOfficialPickProbabilityFloorPct": INDIVIDUAL_GAME_OFFICIAL_PICK_PROBABILITY_FLOOR_PCT,
        "multipleReversalsRequireIndependentConfirmationForOfficialStatus": True,
        "unvalidatedSignalsCauseRecommendationAbstention": True,
        "futureAccuracyGuaranteed": False,
        "patched": patched,
        "errors": errors,
        "policy": (
            "Every game retains a visible immutable winner record. Recommendation eligibility begins with the 60% "
            "direction-integrity gate and now also requires an exact code-reviewed signal signature whose prospective "
            "95% Wilson lower precision bound is at least 70%. The 90% values remain dashboard aspirations. No future "
            "outcome is guaranteed; the system abstains rather than labeling an unvalidated signal as qualified."
        ),
    }
