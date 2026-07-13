from __future__ import annotations

import copy
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.5-90pct-automatic-independent-promotion"
MIN_CLEAN_OFFICIAL = max(500, int(os.environ.get("INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION", "500")))
MIN_UNTOUCHED_TEST = max(100, int(os.environ.get("INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION", "100")))
MIN_PUBLIC_PLAYABLE_EVIDENCE = int(os.environ.get("INQSI_MLB_ML_MIN_PLAYABLE_EVIDENCE_FOR_PUBLIC_CLAIM", "200"))
MIN_TEST_ACCURACY_LIFT_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_TEST_ACCURACY_LIFT_PCT", "1.0"))
MIN_BRIER_SKILL_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_BRIER_SKILL_PCT", "0.1"))
MAX_CALIBRATION_ERROR = min(0.10, float(os.environ.get("INQSI_MLB_ML_MAX_TEST_CALIBRATION_ERROR", "0.08")))
MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT = 90.0
MIN_SELECTED_RELIABILITY_TEST = max(100, int(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST", "100")))
MIN_SELECTED_RELIABILITY_ACCURACY = max(90.0, float(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY", "90")))
MIN_SELECTED_PRICE_COVERAGE = max(90.0, float(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE", "90")))
MAX_RELIABILITY_CALIBRATION_ERROR = min(0.10, float(os.environ.get("INQSI_MLB_ML_MAX_RELIABILITY_CALIBRATION_ERROR", "0.10")))
MIN_ROLLING_24H_SLATE_ACCURACY_PCT = 90.0
RELIABILITY_PROGRESS_MILESTONES_PCT = (50.0, 60.0, 70.0, 80.0)
# Fail-safe default: only the authoritative AWS audit explicitly enables writes.
AUTO_PROMOTE = os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false").lower() in {"1", "true", "yes"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _optional_f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _model(bundle: Dict[str, Any], name: str) -> Dict[str, Any]:
    value = (bundle or {}).get(name) or ((bundle or {}).get("dualModel") or {}).get(name) or {}
    return value if isinstance(value, dict) else {}


def _validated_reliability_threshold(model: Dict[str, Any]) -> tuple[bool, Optional[float], Dict[str, Any]]:
    info = model.get("selectedThreshold") or {}
    threshold = _optional_f(info.get("threshold"))
    valid = bool(
        model.get("ok") is True
        and model.get("thresholdSelectedOnValidationOnly") is True
        and info.get("ok") is True
        and info.get("selectionSource") == "validation_only"
        and threshold is not None
        and 0.0 < threshold < 1.0
    )
    return valid, threshold if valid else None, info


def _threshold_matches_validation(dual_model: Dict[str, Any], reliability_model: Dict[str, Any]) -> bool:
    valid, model_threshold, _ = _validated_reliability_threshold(reliability_model)
    validation_info = ((dual_model.get("validation") or {}).get("selectedReliability") or {})
    validation_threshold = _optional_f(validation_info.get("threshold"))
    return bool(
        valid
        and validation_info.get("ok") is True
        and validation_info.get("selectionSource") == "validation_only"
        and validation_threshold is not None
        and abs(validation_threshold - float(model_threshold)) <= 1e-12
    )


def _block(bucket: List[Dict[str, Any]], code: str, actual: Any, required: Any) -> None:
    bucket.append({"code": code, "actual": actual, "required": required})


def _reliability_progress(rolling_slate_accuracy: Any) -> Dict[str, Any]:
    """Expose rolling-slate progress bands without weakening the 90% gate."""
    actual = _optional_f(rolling_slate_accuracy)
    milestones = [
        {"accuracyPct": milestone, "reached": bool(actual is not None and actual >= milestone)}
        for milestone in RELIABILITY_PROGRESS_MILESTONES_PCT
    ]
    reached = [item["accuracyPct"] for item in milestones if item["reached"]]
    pending = [item["accuracyPct"] for item in milestones if not item["reached"]]
    return {
        "reportingOnly": True,
        "actualRolling24hSlateAccuracyPct": actual,
        "milestones": milestones,
        "highestReachedPct": max(reached) if reached else None,
        "nextMilestonePct": min(pending) if pending else None,
        "promotionTargetPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
        "affectsPromotionEligibility": False,
    }


def evaluate(
    dual_model: Dict[str, Any],
    clean_count: int,
    playable_evidence_count: int,
    rolling_slate_accuracy_pct: Any = None,
) -> Dict[str, Any]:
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
    outcome_model = dual_model.get("outcomeModel") or {}
    reliability_model = dual_model.get("reliabilityModel") or {}
    outcome_model_available = bool(outcome_model.get("ok") is True)
    reliability_model_available = bool(reliability_model.get("ok") is True)
    reliability_threshold_valid, reliability_threshold, reliability_threshold_info = _validated_reliability_threshold(reliability_model)
    reliability_threshold_matches_validation = _threshold_matches_validation(dual_model, reliability_model)
    rolling_slate_accuracy = _optional_f(rolling_slate_accuracy_pct)
    reliability_progress = _reliability_progress(rolling_slate_accuracy)

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
    if not outcome_model_available:
        _block(direction_blockers, "OUTCOME_MODEL_NOT_TRAINED", outcome_model.get("reason") or dual_model.get("status"), "trained outcome model")
    if not reliability_model_available:
        _block(playability_blockers, "RELIABILITY_MODEL_NOT_TRAINED", reliability_model.get("reason") or dual_model.get("status"), "trained reliability model")
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
    if outcome.get("accuracyPct") is None or _f(outcome.get("accuracyPct"), -999.0) < MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT:
        _block(
            direction_blockers,
            "OUTCOME_UNTOUCHED_ACCURACY_BELOW_AUTHORITY_TARGET",
            outcome.get("accuracyPct"),
            MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT,
        )
    if outcome.get("brierSkillPct") is None or _f(outcome.get("brierSkillPct"), -999.0) < MIN_BRIER_SKILL_PCT:
        _block(direction_blockers, "NO_POSITIVE_BRIER_SKILL", outcome.get("brierSkillPct"), MIN_BRIER_SKILL_PCT)
    if outcome.get("logLoss") is None or baseline.get("logLoss") is None or _f(outcome.get("logLoss"), 999.0) >= _f(baseline.get("logLoss"), 0.0):
        _block(direction_blockers, "LOG_LOSS_NOT_BETTER_THAN_MARKET", outcome.get("logLoss"), f"less than {baseline.get('logLoss')}")
    if outcome.get("calibrationError") is None or _f(outcome.get("calibrationError"), 999.0) > MAX_CALIBRATION_ERROR:
        _block(direction_blockers, "CALIBRATION_ERROR_TOO_HIGH", outcome.get("calibrationError"), MAX_CALIBRATION_ERROR)

    selected_count = int(reliability.get("count") or 0)
    selected_accuracy = reliability.get("accuracyPct")
    price_coverage = reliability.get("exactOddsCoveragePct", reliability.get("priceCoveragePct"))
    reliability_calibration = reliability.get("calibrationError")
    if not reliability_threshold_valid or not reliability_threshold_matches_validation:
        _block(
            playability_blockers,
            "RELIABILITY_THRESHOLD_NOT_VALIDATION_SELECTED",
            {
                "threshold": reliability_threshold_info.get("threshold"),
                "thresholdInfoOk": reliability_threshold_info.get("ok"),
                "selectionSource": reliability_threshold_info.get("selectionSource"),
                "thresholdSelectedOnValidationOnly": reliability_model.get("thresholdSelectedOnValidationOnly"),
                "matchesValidationArtifact": reliability_threshold_matches_validation,
            },
            "successful validation_only threshold selection matching the validation artifact",
        )
    if selected_count < MIN_SELECTED_RELIABILITY_TEST:
        _block(playability_blockers, "INSUFFICIENT_SELECTED_RELIABILITY_TEST_ROWS", selected_count, MIN_SELECTED_RELIABILITY_TEST)
    if selected_accuracy is None or _f(selected_accuracy, -999.0) < MIN_SELECTED_RELIABILITY_ACCURACY:
        _block(playability_blockers, "SELECTED_ACCURACY_TOO_LOW", selected_accuracy, MIN_SELECTED_RELIABILITY_ACCURACY)
    if price_coverage is None or _f(price_coverage, -999.0) < MIN_SELECTED_PRICE_COVERAGE:
        _block(playability_blockers, "SELECTED_EXACT_ODDS_COVERAGE_TOO_LOW", price_coverage, MIN_SELECTED_PRICE_COVERAGE)
    if reliability_calibration is None or _f(reliability_calibration, 999.0) > MAX_RELIABILITY_CALIBRATION_ERROR:
        _block(playability_blockers, "RELIABILITY_CALIBRATION_ERROR_TOO_HIGH", reliability_calibration, MAX_RELIABILITY_CALIBRATION_ERROR)
    if rolling_slate_accuracy is None:
        for blockers in (direction_blockers, playability_blockers):
            _block(
                blockers,
                "ROLLING_24H_SLATE_ACCURACY_UNAVAILABLE",
                None,
                f">= {MIN_ROLLING_24H_SLATE_ACCURACY_PCT}",
            )
    elif rolling_slate_accuracy < MIN_ROLLING_24H_SLATE_ACCURACY_PCT:
        for blockers in (direction_blockers, playability_blockers):
            _block(
                blockers,
                "ROLLING_24H_SLATE_ACCURACY_BELOW_TARGET",
                rolling_slate_accuracy,
                MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
            )

    direction_eligible = not direction_blockers
    playability_eligible = not playability_blockers
    any_eligible = direction_eligible or playability_eligible
    public_claim_eligible = playable_evidence_count >= MIN_PUBLIC_PLAYABLE_EVIDENCE
    return {
        "ok": True,
        "version": VERSION,
        "evaluatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "automaticPromotionEnabled": AUTO_PROMOTE,
        "automaticPromotionRequiresPassedApplicableGates": True,
        "promotionEligible": any_eligible,
        "directionPromotionEligible": direction_eligible,
        "playabilityPromotionEligible": playability_eligible,
        "promotionDecision": "PROMOTE" if any_eligible and AUTO_PROMOTE else "ELIGIBLE_BUT_AUTOMATIC_PROMOTION_DISABLED" if any_eligible else "RETAIN_CURRENT_CHAMPION",
        "directionAuthorityEnabled": bool(direction_eligible and AUTO_PROMOTE),
        "playabilityAuthorityEnabled": bool(playability_eligible and AUTO_PROMOTE),
        "directionChecks": {
            **common_checks,
            "outcomeModelAvailable": outcome_model_available,
            "outcomeUntouchedAccuracyPct": outcome.get("accuracyPct"),
            "minimumOutcomeUntouchedAccuracyPct": MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT,
            "outcomeAccuracyLiftPctPoints": outcome.get("accuracyLiftPctPoints"),
            "minimumAccuracyLiftPctPoints": MIN_TEST_ACCURACY_LIFT_PCT,
            "outcomeBrierSkillPct": outcome.get("brierSkillPct"),
            "minimumBrierSkillPct": MIN_BRIER_SKILL_PCT,
            "outcomeLogLoss": outcome.get("logLoss"), "marketLogLoss": baseline.get("logLoss"),
            "outcomeCalibrationError": outcome.get("calibrationError"), "maximumCalibrationError": MAX_CALIBRATION_ERROR,
            "rolling24hSlateAccuracyPct": rolling_slate_accuracy,
            "minimumRolling24hSlateAccuracyPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
            "rolling24hSlateAccuracyAvailable": rolling_slate_accuracy is not None,
        },
        "playabilityChecks": {
            **common_checks,
            "reliabilityModelAvailable": reliability_model_available,
            "reliabilityThresholdValidated": bool(reliability_threshold_valid and reliability_threshold_matches_validation),
            "validationSelectedReliabilityThreshold": reliability_threshold,
            "selectedReliabilityTestCount": selected_count,
            "minimumSelectedReliabilityTest": MIN_SELECTED_RELIABILITY_TEST,
            "selectedAccuracyPct": selected_accuracy,
            "minimumSelectedAccuracyPct": MIN_SELECTED_RELIABILITY_ACCURACY,
            "selectedExactOddsCoveragePct": price_coverage,
            "minimumSelectedExactOddsCoveragePct": MIN_SELECTED_PRICE_COVERAGE,
            "selectedCalibrationError": reliability_calibration,
            "maximumSelectedCalibrationError": MAX_RELIABILITY_CALIBRATION_ERROR,
            "rolling24hSlateAccuracyPct": rolling_slate_accuracy,
            "minimumRolling24hSlateAccuracyPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
            "rolling24hSlateAccuracyAvailable": rolling_slate_accuracy is not None,
            "rolling24hSlateAccuracyProgress": reliability_progress,
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
        "rolling24hSlateAccuracyProgress": reliability_progress,
        "policy": "Direction and playability earn authority independently and are promoted automatically only after their applicable gates pass. Both require a current rolling 24-hour official-card MLB slate accuracy average of at least 90%, at least 500 clean rows, and at least 100 total untouched-test rows. Direction additionally requires at least 90% untouched outcome accuracy, market lift, and calibration/baseline gates. Playability additionally requires at least 100 selected untouched-test rows, at least 90% selected accuracy, at least 90% exact locked-odds coverage, and calibration error no greater than 0.10. The 50/60/70/80 rolling-slate milestones are shadow/reporting-only and never activate authority.",
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


def _champion_payload_errors(bundle: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if bundle.get("directionAuthorityEnabled") is True and _model(bundle, "outcomeModel").get("ok") is not True:
        errors.append("direction_authority_requires_valid_outcome_model")
    if bundle.get("playabilityAuthorityEnabled") is True:
        reliability_model = _model(bundle, "reliabilityModel")
        if reliability_model.get("ok") is not True:
            errors.append("playability_authority_requires_valid_reliability_model")
        threshold_valid, _, _ = _validated_reliability_threshold(reliability_model)
        if not threshold_valid:
            errors.append("playability_authority_requires_validation_selected_reliability_threshold")
    return errors


def _set_authority_model(payload: Dict[str, Any], name: str, model: Dict[str, Any]) -> None:
    dual = payload.setdefault("dualModel", {})
    if model:
        copied = copy.deepcopy(model)
        payload[name] = copied
        dual[name] = copy.deepcopy(copied)
    else:
        payload.pop(name, None)
        dual.pop(name, None)


def _authority_payload(
    challenger: Dict[str, Any],
    current: Dict[str, Any],
    promote_direction: bool,
    promote_playability: bool,
) -> tuple[Optional[Dict[str, Any]], List[str]]:
    payload = copy.deepcopy(challenger)
    direction_enabled = bool(promote_direction or current.get("directionAuthorityEnabled"))
    playability_enabled = bool(promote_playability or current.get("playabilityAuthorityEnabled"))

    # A partial promotion may only replace the artifact for the eligible
    # authority. The other artifact always comes from the existing champion,
    # even when that authority is currently disabled (it may still be useful as
    # shadow evidence and must never be silently swapped for the latest model).
    outcome_model = _model(challenger, "outcomeModel") if promote_direction else _model(current, "outcomeModel")
    reliability_model = _model(challenger, "reliabilityModel") if promote_playability else _model(current, "reliabilityModel")

    _set_authority_model(payload, "outcomeModel", outcome_model)
    _set_authority_model(payload, "reliabilityModel", reliability_model)
    payload["directionAuthorityEnabled"] = direction_enabled
    payload["playabilityAuthorityEnabled"] = playability_enabled
    payload["authorityModelSources"] = {
        "outcomeModel": "latest_gate_eligible_challenger" if promote_direction else "existing_champion" if outcome_model else None,
        "reliabilityModel": "latest_gate_eligible_challenger" if promote_playability else "existing_champion" if reliability_model else None,
    }
    payload["partialPromotionModelIsolationApplied"] = True
    errors = _champion_payload_errors(payload)
    return (payload if not errors else None), errors


def _store_champion(bundle: Dict[str, Any], approval_mode: str) -> Dict[str, Any]:
    safety_errors = _champion_payload_errors(bundle)
    if safety_errors:
        return {
            "ok": False,
            "promoted": False,
            "error": "unsafe champion payload rejected",
            "safetyErrors": safety_errors,
        }
    table, history = _ddb_table()
    if table is None or history is None:
        return {"ok": False, "promoted": False, "error": "SNAPSHOTS_TABLE not configured"}
    payload = copy.deepcopy(bundle)
    payload["mode"] = (
        "APPROVED_CHAMPION"
        if payload.get("directionAuthorityEnabled") is True or payload.get("playabilityAuthorityEnabled") is True
        else str(payload.get("mode") or "SHADOW_CHAMPION")
    )
    payload["approvedAtUtc"] = datetime.now(timezone.utc).isoformat()
    payload["approvalMode"] = approval_mode
    champion = history.ddb_safe({"PK": "MLB_ML_OPTIMIZATION#V3", "SK": "CHAMPION", "record_type": "mlb_ml_optimization_champion", "sport": "mlb", "created_at": payload["approvedAtUtc"], "data": payload})
    table.put_item(Item=champion)
    return {"ok": True, "promoted": True, "pk": champion["PK"], "sk": champion["SK"], "directionAuthorityEnabled": payload.get("directionAuthorityEnabled"), "playabilityAuthorityEnabled": payload.get("playabilityAuthorityEnabled")}


def _rolling_slate_authority_blocked(decision: Dict[str, Any]) -> bool:
    rolling_codes = {
        "ROLLING_24H_SLATE_ACCURACY_UNAVAILABLE",
        "ROLLING_24H_SLATE_ACCURACY_BELOW_TARGET",
    }
    return any(
        str(item.get("code") or "") in rolling_codes
        for item in (decision.get("blockers") or [])
        if isinstance(item, dict)
    )


def _suspend_current_authorities(decision: Dict[str, Any]) -> Dict[str, Any]:
    current = load_champion() or {}
    if not current or not (
        current.get("directionAuthorityEnabled") is True
        or current.get("playabilityAuthorityEnabled") is True
    ):
        return {
            "ok": True,
            "promoted": False,
            "suspended": False,
            "reason": "rolling_slate_gate_failed_no_enabled_authority_to_suspend",
        }
    payload = copy.deepcopy(current)
    payload["directionAuthorityEnabled"] = False
    payload["playabilityAuthorityEnabled"] = False
    payload["mode"] = "SHADOW_SUSPENDED_ROLLING_24H_SLATE_BELOW_AUTHORITY_TARGET"
    payload["automaticAuthoritySuspension"] = {
        "applied": True,
        "reason": "rolling_24h_official_card_slate_accuracy_missing_or_below_90pct",
        "preservedModelArtifactsForShadow": True,
        "promotionGateVersion": decision.get("version"),
        "blockers": [
            item for item in (decision.get("blockers") or [])
            if isinstance(item, dict) and str(item.get("code") or "").startswith("ROLLING_24H_SLATE_ACCURACY_")
        ],
    }
    result = _store_champion(payload, "automatic_rolling_slate_authority_suspension")
    result["promoted"] = False
    result["suspended"] = result.get("ok") is True
    result["reason"] = "enabled_authorities_suspended_below_90pct_rolling_slate_target"
    return result


def promote_if_allowed(bundle: Dict[str, Any]) -> Dict[str, Any]:
    decision = bundle.get("promotionGate") or {}
    if not AUTO_PROMOTE:
        return {"ok": True, "promoted": False, "reason": "automatic_promotion_disabled_or_not_eligible"}
    if _rolling_slate_authority_blocked(decision):
        return _suspend_current_authorities(decision)
    if decision.get("promotionDecision") != "PROMOTE":
        return {"ok": True, "promoted": False, "reason": "automatic_promotion_disabled_or_not_eligible"}
    promote_direction = decision.get("directionPromotionEligible") is True
    promote_playability = decision.get("playabilityPromotionEligible") is True
    if not promote_direction and not promote_playability:
        return {"ok": True, "promoted": False, "reason": "no_applicable_promotion_gate_passed"}
    payload, errors = _authority_payload(
        bundle,
        load_champion() or {},
        promote_direction,
        promote_playability,
    )
    if errors or payload is None:
        return {"ok": False, "promoted": False, "error": "unsafe automatic promotion payload", "safetyErrors": errors}
    return _store_champion(payload, "automatic_gate_promotion")


def promote_reviewed_latest(authority: str = "both") -> Dict[str, Any]:
    """Deprecated compatibility entry point; automatic gated audit is authoritative."""
    return {
        "ok": True,
        "promoted": False,
        "deprecated": True,
        "requestedAuthority": authority,
        "reason": "legacy_manual_promotion_disabled_use_authoritative_automatic_gate",
    }


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
