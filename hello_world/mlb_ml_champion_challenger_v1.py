from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.1-separate-direction-playability-gates"
MIN_CLEAN_OFFICIAL = int(os.environ.get("INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION", "500"))
MIN_UNTOUCHED_TEST = int(os.environ.get("INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION", "100"))
MIN_PUBLIC_PLAYABLE_EVIDENCE = int(os.environ.get("INQSI_MLB_ML_MIN_PLAYABLE_EVIDENCE_FOR_PUBLIC_CLAIM", "200"))
MIN_TEST_ACCURACY_LIFT_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_TEST_ACCURACY_LIFT_PCT", "1.0"))
MIN_BRIER_SKILL_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_BRIER_SKILL_PCT", "0.1"))
MAX_CALIBRATION_ERROR = float(os.environ.get("INQSI_MLB_ML_MAX_TEST_CALIBRATION_ERROR", "0.08"))
MIN_SELECTED_RELIABILITY_TEST = int(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST", "50"))
MIN_SELECTED_RELIABILITY_ACCURACY = float(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY", "60"))
MIN_SELECTED_PRICE_COVERAGE = float(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE", "90"))
MIN_SELECTED_ROI_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_ROI_PCT", "0"))
MAX_RELIABILITY_CALIBRATION_ERROR = float(os.environ.get("INQSI_MLB_ML_MAX_RELIABILITY_CALIBRATION_ERROR", "0.10"))
AUTO_PROMOTE = os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false").lower() in {"1", "true", "yes"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _block(bucket: List[Dict[str, Any]], code: str, actual: Any, required: Any) -> None:
    bucket.append({"code": code, "actual": actual, "required": required})


def evaluate(dual_model: Dict[str, Any], clean_count: int, playable_evidence_count: int) -> Dict[str, Any]:
    direction_blockers: List[Dict[str, Any]] = []
    playability_blockers: List[Dict[str, Any]] = []
    untouched = dual_model.get("untouchedTest") or {}
    outcome = untouched.get("outcome") or {}
    reliability = untouched.get("selectedReliability") or {}
    baseline = outcome.get("baseline") or {}
    split = dual_model.get("split") or {}
    counts = split.get("counts") or {}
    test_count = int(counts.get("test") or outcome.get("count") or 0)
    data_quality = dual_model.get("dataQuality") or {}

    common_checks = {
        "cleanOfficialCount": clean_count,
        "minimumCleanOfficial": MIN_CLEAN_OFFICIAL,
        "untouchedTestCount": test_count,
        "minimumUntouchedTest": MIN_UNTOUCHED_TEST,
        "testWasUntouched": dual_model.get("testWasUntouchedDuringFitAndThresholdSelection"),
        "validationProtocol": "threshold_selected_on_validation_only_test_untouched",
        "modelScope": data_quality.get("modelScope"),
        "averageFundamentalsCompletenessPct": data_quality.get("averageFundamentalsCompletenessPct"),
    }
    if not dual_model.get("ok"):
        _block(direction_blockers, "CHALLENGER_NOT_TRAINED", dual_model.get("status"), "trained dual model")
        _block(playability_blockers, "CHALLENGER_NOT_TRAINED", dual_model.get("status"), "trained dual model")
    if clean_count < MIN_CLEAN_OFFICIAL:
        _block(direction_blockers, "INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE", clean_count, MIN_CLEAN_OFFICIAL)
        _block(playability_blockers, "INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE", clean_count, MIN_CLEAN_OFFICIAL)
    if test_count < MIN_UNTOUCHED_TEST:
        _block(direction_blockers, "INSUFFICIENT_UNTOUCHED_TEST", test_count, MIN_UNTOUCHED_TEST)
        _block(playability_blockers, "INSUFFICIENT_UNTOUCHED_TEST", test_count, MIN_UNTOUCHED_TEST)
    if dual_model.get("testWasUntouchedDuringFitAndThresholdSelection") is not True:
        _block(direction_blockers, "TEST_NOT_PROVEN_UNTOUCHED", dual_model.get("testWasUntouchedDuringFitAndThresholdSelection"), True)
        _block(playability_blockers, "TEST_NOT_PROVEN_UNTOUCHED", dual_model.get("testWasUntouchedDuringFitAndThresholdSelection"), True)

    if outcome.get("accuracyLiftPctPoints") is None or _f(outcome.get("accuracyLiftPctPoints"), -999.0) < MIN_TEST_ACCURACY_LIFT_PCT:
        _block(direction_blockers, "DOES_NOT_BEAT_MARKET_ACCURACY", outcome.get("accuracyLiftPctPoints"), MIN_TEST_ACCURACY_LIFT_PCT)
    if outcome.get("brierSkillPct") is None or _f(outcome.get("brierSkillPct"), -999.0) < MIN_BRIER_SKILL_PCT:
        _block(direction_blockers, "NO_POSITIVE_BRIER_SKILL", outcome.get("brierSkillPct"), MIN_BRIER_SKILL_PCT)
    if outcome.get("logLoss") is None or baseline.get("logLoss") is None or _f(outcome.get("logLoss"), 999.0) >= _f(baseline.get("logLoss"), 0.0):
        _block(direction_blockers, "LOG_LOSS_NOT_BETTER_THAN_MARKET", outcome.get("logLoss"), f"less than {baseline.get('logLoss')}")
    if outcome.get("calibrationError") is None or _f(outcome.get("calibrationError"), 999.0) > MAX_CALIBRATION_ERROR:
        _block(direction_blockers, "CALIBRATION_ERROR_TOO_HIGH", outcome.get("calibrationError"), MAX_CALIBRATION_ERROR)

    selected_count = int(reliability.get("count") or 0)
    selected_accuracy = reliability.get("accuracyPct")
    price_coverage = reliability.get("priceCoveragePct")
    selected_roi = reliability.get("flatUnitRoiPct")
    reliability_calibration = reliability.get("calibrationError")
    if selected_count < MIN_SELECTED_RELIABILITY_TEST:
        _block(playability_blockers, "INSUFFICIENT_SELECTED_RELIABILITY_TEST_ROWS", selected_count, MIN_SELECTED_RELIABILITY_TEST)
    if selected_accuracy is None or _f(selected_accuracy, -999.0) < MIN_SELECTED_RELIABILITY_ACCURACY:
        _block(playability_blockers, "SELECTED_ACCURACY_TOO_LOW", selected_accuracy, MIN_SELECTED_RELIABILITY_ACCURACY)
    if price_coverage is None or _f(price_coverage, -999.0) < MIN_SELECTED_PRICE_COVERAGE:
        _block(playability_blockers, "SELECTED_PRICE_COVERAGE_TOO_LOW", price_coverage, MIN_SELECTED_PRICE_COVERAGE)
    if selected_roi is None or _f(selected_roi, -999.0) <= MIN_SELECTED_ROI_PCT:
        _block(playability_blockers, "SELECTED_ROI_NOT_POSITIVE", selected_roi, f"> {MIN_SELECTED_ROI_PCT}")
    if reliability_calibration is None or _f(reliability_calibration, 999.0) > MAX_RELIABILITY_CALIBRATION_ERROR:
        _block(playability_blockers, "RELIABILITY_CALIBRATION_ERROR_TOO_HIGH", reliability_calibration, MAX_RELIABILITY_CALIBRATION_ERROR)

    direction_eligible = not direction_blockers
    playability_eligible = not playability_blockers
    any_eligible = direction_eligible or playability_eligible
    public_claim_eligible = playable_evidence_count >= MIN_PUBLIC_PLAYABLE_EVIDENCE
    return {
        "ok": True,
        "version": VERSION,
        "evaluatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "automaticPromotionEnabled": AUTO_PROMOTE,
        "promotionEligible": any_eligible,
        "directionPromotionEligible": direction_eligible,
        "playabilityPromotionEligible": playability_eligible,
        "promotionDecision": "PROMOTE" if any_eligible and AUTO_PROMOTE else "READY_FOR_REVIEW" if any_eligible else "RETAIN_CURRENT_CHAMPION",
        "directionAuthorityEnabled": bool(direction_eligible and AUTO_PROMOTE),
        "playabilityAuthorityEnabled": bool(playability_eligible and AUTO_PROMOTE),
        "directionChecks": {
            **common_checks,
            "outcomeAccuracyLiftPctPoints": outcome.get("accuracyLiftPctPoints"),
            "minimumAccuracyLiftPctPoints": MIN_TEST_ACCURACY_LIFT_PCT,
            "outcomeBrierSkillPct": outcome.get("brierSkillPct"),
            "minimumBrierSkillPct": MIN_BRIER_SKILL_PCT,
            "outcomeLogLoss": outcome.get("logLoss"), "marketLogLoss": baseline.get("logLoss"),
            "outcomeCalibrationError": outcome.get("calibrationError"), "maximumCalibrationError": MAX_CALIBRATION_ERROR,
        },
        "playabilityChecks": {
            **common_checks,
            "selectedReliabilityTestCount": selected_count,
            "minimumSelectedReliabilityTest": MIN_SELECTED_RELIABILITY_TEST,
            "selectedAccuracyPct": selected_accuracy,
            "minimumSelectedAccuracyPct": MIN_SELECTED_RELIABILITY_ACCURACY,
            "selectedPriceCoveragePct": price_coverage,
            "minimumSelectedPriceCoveragePct": MIN_SELECTED_PRICE_COVERAGE,
            "selectedFlatUnitRoiPct": selected_roi,
            "minimumSelectedFlatUnitRoiPct": f"> {MIN_SELECTED_ROI_PCT}",
            "selectedCalibrationError": reliability_calibration,
            "maximumSelectedCalibrationError": MAX_RELIABILITY_CALIBRATION_ERROR,
        },
        "directionBlockers": direction_blockers,
        "playabilityBlockers": playability_blockers,
        "blockers": direction_blockers + [b for b in playability_blockers if b not in direction_blockers],
        "publicPlayableClaim": {
            "eligible": public_claim_eligible,
            "actualSettledPlayableRecommendations": playable_evidence_count,
            "required": MIN_PUBLIC_PLAYABLE_EVIDENCE,
            "policy": "Two hundred settled production playable recommendations are required for a credible public playable-performance claim, but this is not a circular prerequisite for an initially validated playability champion.",
        },
        "policy": "Direction and playability earn authority independently. Direction must beat the market on untouched chronological data. Playability must meet untouched selected-sample accuracy, calibration, price coverage, and positive ROI gates.",
    }


def _ddb_table():
    try:
        import inqsi_pull_history as history
        return history.PULLS, history
    except Exception:
        return None, None


def store_challenger(bundle: Dict[str, Any]) -> Dict[str, Any]:
    table, history = _ddb_table()
    if table is None or history is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    created = bundle.get("createdAtUtc") or datetime.now(timezone.utc).isoformat()
    latest = history.ddb_safe({"PK": "MLB_ML_OPTIMIZATION#V3", "SK": "CHALLENGER#LATEST", "record_type": "mlb_ml_optimization_challenger_latest", "sport": "mlb", "created_at": created, "data": bundle})
    dated = history.ddb_safe({"PK": "MLB_ML_OPTIMIZATION#V3", "SK": f"CHALLENGER#{created}", "record_type": "mlb_ml_optimization_challenger_run", "sport": "mlb", "created_at": created, "data": bundle})
    table.put_item(Item=latest); table.put_item(Item=dated)
    return {"ok": True, "latestPk": latest["PK"], "latestSk": latest["SK"], "runSk": dated["SK"]}


def _store_champion(bundle: Dict[str, Any], approval_mode: str) -> Dict[str, Any]:
    table, history = _ddb_table()
    if table is None or history is None:
        return {"ok": False, "promoted": False, "error": "SNAPSHOTS_TABLE not configured"}
    payload = copy.deepcopy(bundle)
    payload["mode"] = "APPROVED_CHAMPION"
    payload["approvedAtUtc"] = datetime.now(timezone.utc).isoformat()
    payload["approvalMode"] = approval_mode
    champion = history.ddb_safe({"PK": "MLB_ML_OPTIMIZATION#V3", "SK": "CHAMPION", "record_type": "mlb_ml_optimization_champion", "sport": "mlb", "created_at": payload["approvedAtUtc"], "data": payload})
    table.put_item(Item=champion)
    return {"ok": True, "promoted": True, "pk": champion["PK"], "sk": champion["SK"], "directionAuthorityEnabled": payload.get("directionAuthorityEnabled"), "playabilityAuthorityEnabled": payload.get("playabilityAuthorityEnabled")}


def promote_if_allowed(bundle: Dict[str, Any]) -> Dict[str, Any]:
    decision = bundle.get("promotionGate") or {}
    if not AUTO_PROMOTE or decision.get("promotionDecision") != "PROMOTE":
        return {"ok": True, "promoted": False, "reason": "automatic_promotion_disabled_or_not_eligible"}
    payload = copy.deepcopy(bundle)
    payload["directionAuthorityEnabled"] = bool(decision.get("directionPromotionEligible"))
    payload["playabilityAuthorityEnabled"] = bool(decision.get("playabilityPromotionEligible"))
    return _store_champion(payload, "automatic_gate_promotion")


def promote_reviewed_latest(authority: str = "both") -> Dict[str, Any]:
    if authority not in {"direction", "playability", "both"}:
        return {"ok": False, "promoted": False, "error": "authority must be direction, playability, or both"}
    table, _ = _ddb_table()
    if table is None:
        return {"ok": False, "promoted": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = table.get_item(Key={"PK": "MLB_ML_OPTIMIZATION#V3", "SK": "CHALLENGER#LATEST"}, ConsistentRead=True).get("Item") or {}
    bundle = item.get("data") or {}
    gate = bundle.get("promotionGate") or {}
    direction = authority in {"direction", "both"}
    playability = authority in {"playability", "both"}
    if direction and gate.get("directionPromotionEligible") is not True:
        return {"ok": False, "promoted": False, "error": "latest challenger has not passed direction promotion gates", "blockers": gate.get("directionBlockers")}
    if playability and gate.get("playabilityPromotionEligible") is not True:
        return {"ok": False, "promoted": False, "error": "latest challenger has not passed playability promotion gates", "blockers": gate.get("playabilityBlockers")}
    current = load_champion() or {}
    payload = copy.deepcopy(bundle)
    payload["directionAuthorityEnabled"] = bool(direction or current.get("directionAuthorityEnabled"))
    payload["playabilityAuthorityEnabled"] = bool(playability or current.get("playabilityAuthorityEnabled"))
    payload["manualReviewConfirmed"] = True
    payload["manualReviewAuthorityRequested"] = authority
    return _store_champion(payload, "manual_reviewed_challenger_promotion")


def load_champion() -> Optional[Dict[str, Any]]:
    table, _ = _ddb_table()
    if table is None:
        return None
    try:
        item = table.get_item(Key={"PK": "MLB_ML_OPTIMIZATION#V3", "SK": "CHAMPION"}, ConsistentRead=True).get("Item") or {}
        data = item.get("data") or {}
        return data if isinstance(data, dict) else None
    except Exception:
        return None
