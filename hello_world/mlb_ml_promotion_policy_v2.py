from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import mlb_ml_experiment_v2 as experiment


VERSION = "MLB-ML-PROMOTION-POLICY-v2-prospective-market-skill-manual-first"
MIN_TOTAL_CLEAN_ROWS = 500
MIN_PROSPECTIVE_TEST_ROWS = 100
MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS = 100
MIN_ACCURACY_LIFT_PCT_POINTS = 1.0
MAX_CALIBRATION_ERROR = 0.08
ASPIRATIONAL_ACCURACY_PCT = 90.0


def _number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if parsed == parsed and abs(parsed) != float("inf") else None
    except Exception:
        return None


def _block(target: List[Dict[str, Any]], code: str, actual: Any, required: Any) -> None:
    target.append({"code": code, "actual": actual, "required": required})


def _manifest_errors(manifest: Dict[str, Any], dual_model: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if manifest.get("version") != experiment.VERSION:
        errors.append("wrong_experiment_manifest_version")
    try:
        if manifest.get("manifestDigest") != experiment.manifest_digest(manifest):
            errors.append("experiment_manifest_digest_mismatch")
    except Exception:
        errors.append("experiment_manifest_digest_invalid")
    if manifest.get("prospectiveTestSealed") is not True:
        errors.append("prospective_test_not_sealed")
    if dual_model.get("experimentId") != manifest.get("experimentId"):
        errors.append("model_experiment_id_mismatch")
    if dual_model.get("experimentManifestDigest") != manifest.get("manifestDigest"):
        errors.append("model_experiment_manifest_digest_mismatch")
    if dual_model.get("featureSchemaFingerprint") != manifest.get("featureSchemaFingerprint"):
        errors.append("model_feature_schema_fingerprint_mismatch")
    if dual_model.get("testWasUntouchedDuringFitAndThresholdSelection") is not True:
        errors.append("prospective_test_not_proven_untouched")
    return sorted(set(errors))


def evaluate(
    dual_model: Dict[str, Any],
    manifest: Dict[str, Any],
    *,
    current_champion: Optional[Dict[str, Any]] = None,
    automatic_promotion_enabled: bool = False,
) -> Dict[str, Any]:
    """Evaluate one sealed, prospective challenger without activating it.

    Ninety-percent slate accuracy is retained as a dashboard aspiration only.
    It never gates promotion and a bad single day never suspends an authority.
    """
    direction_blockers: List[Dict[str, Any]] = []
    playability_blockers: List[Dict[str, Any]] = []
    manifest_errors = _manifest_errors(manifest, dual_model)
    for reason in manifest_errors:
        _block(direction_blockers, reason.upper(), reason, "valid immutable V2 experiment")
        _block(playability_blockers, reason.upper(), reason, "valid immutable V2 experiment")

    split_counts = (dual_model.get("split") or {}).get("counts") or {}
    total_clean = sum(int(split_counts.get(name) or 0) for name in experiment.PARTITION_ORDER)
    prospective_count = int(
        split_counts.get("prospectiveTest")
        or ((dual_model.get("prospectiveTest") or {}).get("outcome") or {}).get("count")
        or 0
    )
    if total_clean < MIN_TOTAL_CLEAN_ROWS:
        for blockers in (direction_blockers, playability_blockers):
            _block(
                blockers,
                "INSUFFICIENT_NEW_COHORT_ROWS",
                total_clean,
                MIN_TOTAL_CLEAN_ROWS,
            )
    if prospective_count < MIN_PROSPECTIVE_TEST_ROWS:
        for blockers in (direction_blockers, playability_blockers):
            _block(
                blockers,
                "INSUFFICIENT_SEALED_PROSPECTIVE_TEST_ROWS",
                prospective_count,
                MIN_PROSPECTIVE_TEST_ROWS,
            )

    outcome_model = dual_model.get("outcomeModel") or {}
    reliability_model = dual_model.get("reliabilityModel") or {}
    prospective = dual_model.get("prospectiveTest") or {}
    outcome = prospective.get("outcome") or {}
    market = outcome.get("baseline") or {}
    paired = outcome.get("pairedAccuracyRegression") or {}
    if outcome_model.get("ok") is not True:
        _block(
            direction_blockers,
            "OUTCOME_MODEL_NOT_TRAINED",
            outcome_model.get("reason"),
            "regularized model trained on frozen train partition",
        )

    brier_skill = _number(outcome.get("brierSkillPct"))
    if brier_skill is None or brier_skill <= 0.0:
        _block(direction_blockers, "NO_POSITIVE_BRIER_SKILL", brier_skill, "> 0")
    model_log_loss = _number(outcome.get("logLoss"))
    market_log_loss = _number(market.get("logLoss"))
    if (
        model_log_loss is None
        or market_log_loss is None
        or model_log_loss >= market_log_loss
    ):
        _block(
            direction_blockers,
            "LOG_LOSS_NOT_LOWER_THAN_SAME_TIME_MARKET",
            model_log_loss,
            f"< {market_log_loss}",
        )
    calibration = _number(outcome.get("calibrationError"))
    if calibration is None or calibration > MAX_CALIBRATION_ERROR:
        _block(
            direction_blockers,
            "CALIBRATION_ERROR_TOO_HIGH",
            calibration,
            f"<= {MAX_CALIBRATION_ERROR}",
        )
    accuracy_lift = _number(outcome.get("accuracyLiftPctPoints"))
    if accuracy_lift is None or accuracy_lift < MIN_ACCURACY_LIFT_PCT_POINTS:
        _block(
            direction_blockers,
            "ACCURACY_LIFT_TOO_LOW",
            accuracy_lift,
            f">= {MIN_ACCURACY_LIFT_PCT_POINTS}",
        )
    if paired.get("ok") is not True:
        _block(
            direction_blockers,
            "PAIRED_MARKET_REGRESSION_TEST_MISSING",
            paired.get("ok"),
            True,
        )
    elif paired.get("statisticallySignificantRegression") is True:
        _block(
            direction_blockers,
            "STATISTICALLY_SIGNIFICANT_ACCURACY_REGRESSION",
            paired.get("regressionPValue"),
            "no paired regression at alpha 0.05",
        )

    if reliability_model.get("ok") is not True:
        _block(
            playability_blockers,
            "RELIABILITY_MODEL_NOT_TRAINED",
            reliability_model.get("reason"),
            "regularized model trained on frozen train partition",
        )
    threshold = reliability_model.get("selectedThreshold") or {}
    if (
        reliability_model.get("thresholdSelectedOnValidationOnly") is not True
        or threshold.get("ok") is not True
        or threshold.get("selectionSource") != "validation_only"
    ):
        _block(
            playability_blockers,
            "RELIABILITY_THRESHOLD_NOT_VALIDATION_SELECTED",
            threshold,
            "successful validation_only threshold",
        )
    selected = prospective.get("selectedReliability") or {}
    selection_ledger = dual_model.get("prospectiveSelectionLedger") or {}
    if selection_ledger.get("ok") is not True:
        _block(
            playability_blockers,
            "PROSPECTIVE_SELECTION_LEDGER_INVALID",
            selection_ledger.get("conflicts") or selection_ledger.get("ok"),
            "immutable pre-outcome selection ledger with no contract conflicts",
        )
    selected_count = int(
        dual_model.get("prospectiveSelectedRecommendationCount")
        or selected.get("count")
        or 0
    )
    if selected_count < MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS:
        _block(
            playability_blockers,
            "INSUFFICIENT_PROSPECTIVE_SELECTED_RECOMMENDATIONS",
            selected_count,
            MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS,
        )
    selected_calibration = _number(selected.get("calibrationError"))
    if selected_calibration is None or selected_calibration > MAX_CALIBRATION_ERROR:
        _block(
            playability_blockers,
            "SELECTED_RELIABILITY_CALIBRATION_TOO_HIGH",
            selected_calibration,
            f"<= {MAX_CALIBRATION_ERROR}",
        )

    direction_eligible = not direction_blockers
    playability_eligible = not playability_blockers
    any_eligible = direction_eligible or playability_eligible
    champion = current_champion or {}
    stable_champion_exists = bool(
        champion
        and champion.get("stableChampion") is True
        and champion.get("artifactDigest")
    )
    first_manual_review_required = not stable_champion_exists
    if not any_eligible:
        decision = "RETAIN_CURRENT_CHAMPION"
    elif first_manual_review_required:
        decision = "PENDING_MANUAL_FIRST_SHADOW_APPROVAL"
    elif automatic_promotion_enabled:
        decision = "AUTO_SHADOW_APPROVAL_ELIGIBLE"
    else:
        decision = "ELIGIBLE_AUTOMATION_DISABLED"

    outcome_accuracy = _number(outcome.get("accuracyPct"))
    return {
        "ok": True,
        "version": VERSION,
        "evaluatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "experimentId": manifest.get("experimentId"),
        "experimentManifestDigest": manifest.get("manifestDigest"),
        "featureSchemaFingerprint": manifest.get("featureSchemaFingerprint"),
        "directionPromotionEligible": direction_eligible,
        "playabilityPromotionEligible": playability_eligible,
        "promotionEligible": any_eligible,
        "shadowApprovalEligible": any_eligible,
        "runtimeAuthorityActivationEligible": False,
        "promotionDecision": decision,
        "automaticPromotionEnabled": bool(automatic_promotion_enabled),
        "stableChampionExists": stable_champion_exists,
        "firstPromotionRequiresManualReview": first_manual_review_required,
        "directionBlockers": direction_blockers,
        "playabilityBlockers": playability_blockers,
        "blockers": direction_blockers
        + [item for item in playability_blockers if item not in direction_blockers],
        "checks": {
            "totalNewCohortRows": total_clean,
            "minimumTotalNewCohortRows": MIN_TOTAL_CLEAN_ROWS,
            "prospectiveTestRows": prospective_count,
            "minimumProspectiveTestRows": MIN_PROSPECTIVE_TEST_ROWS,
            "prospectiveSelectedRecommendationCount": selected_count,
            "minimumProspectiveSelectedRecommendations": MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS,
            "outcomeBrierSkillPct": brier_skill,
            "outcomeLogLoss": model_log_loss,
            "sameTimeMarketLogLoss": market_log_loss,
            "outcomeCalibrationError": calibration,
            "maximumCalibrationError": MAX_CALIBRATION_ERROR,
            "outcomeAccuracyLiftPctPoints": accuracy_lift,
            "minimumAccuracyLiftPctPoints": MIN_ACCURACY_LIFT_PCT_POINTS,
            "pairedAccuracyRegression": paired,
        },
        "aspirationalDashboard": {
            "accuracyPct": outcome_accuracy,
            "targetAccuracyPct": ASPIRATIONAL_ACCURACY_PCT,
            "targetMet": bool(
                outcome_accuracy is not None
                and outcome_accuracy >= ASPIRATIONAL_ACCURACY_PCT
            ),
            "affectsPromotion": False,
            "affectsAuthoritySuspension": False,
        },
        "policy": (
            "The first V2 approval is manual and creates a shadow-only pointer. "
            "Until a separately deployed V2 inference consumer validates the "
            "artifact, neither approval nor automatic replacement activates "
            "direction or playability authority. A rolling or single-day 90% "
            "result is dashboard-only and never activates or suspends authority."
        ),
    }


def approved_authorities(
    decision: Dict[str, Any], requested: Sequence[str]
) -> List[str]:
    allowed: List[str] = []
    requested_set = {str(value).strip().lower() for value in requested}
    if (
        "direction" in requested_set
        and decision.get("directionPromotionEligible") is True
    ):
        allowed.append("direction")
    if (
        "playability" in requested_set
        and decision.get("playabilityPromotionEligible") is True
    ):
        allowed.append("playability")
    return allowed
