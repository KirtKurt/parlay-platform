from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1-market-baseline-gated"
MIN_CLEAN_OFFICIAL = int(os.environ.get("INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION", "500"))
MIN_UNTOUCHED_TEST = int(os.environ.get("INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION", "100"))
MIN_PLAYABLE_EVIDENCE = int(os.environ.get("INQSI_MLB_ML_MIN_PLAYABLE_EVIDENCE_FOR_PROMOTION", "200"))
MIN_TEST_ACCURACY_LIFT_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_TEST_ACCURACY_LIFT_PCT", "1.0"))
MIN_BRIER_SKILL_PCT = float(os.environ.get("INQSI_MLB_ML_MIN_BRIER_SKILL_PCT", "0.1"))
MAX_CALIBRATION_ERROR = float(os.environ.get("INQSI_MLB_ML_MAX_TEST_CALIBRATION_ERROR", "0.08"))
AUTO_PROMOTE = os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false").lower() in {"1", "true", "yes"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def evaluate(dual_model: Dict[str, Any], clean_count: int, playable_evidence_count: int) -> Dict[str, Any]:
    blockers: List[Dict[str, Any]] = []
    untouched = dual_model.get("untouchedTest") or {}
    outcome = untouched.get("outcome") or {}
    reliability = untouched.get("selectedReliability") or {}
    baseline = outcome.get("baseline") or {}
    split = dual_model.get("split") or {}
    counts = split.get("counts") or {}
    test_count = int(counts.get("test") or outcome.get("count") or 0)

    checks = {
        "cleanOfficialCount": clean_count,
        "minimumCleanOfficial": MIN_CLEAN_OFFICIAL,
        "untouchedTestCount": test_count,
        "minimumUntouchedTest": MIN_UNTOUCHED_TEST,
        "playableEvidenceCount": playable_evidence_count,
        "minimumPlayableEvidence": MIN_PLAYABLE_EVIDENCE,
        "outcomeAccuracyLiftPctPoints": outcome.get("accuracyLiftPctPoints"),
        "minimumAccuracyLiftPctPoints": MIN_TEST_ACCURACY_LIFT_PCT,
        "outcomeBrierSkillPct": outcome.get("brierSkillPct"),
        "minimumBrierSkillPct": MIN_BRIER_SKILL_PCT,
        "outcomeLogLoss": outcome.get("logLoss"),
        "marketLogLoss": baseline.get("logLoss"),
        "outcomeCalibrationError": outcome.get("calibrationError"),
        "maximumCalibrationError": MAX_CALIBRATION_ERROR,
        "selectedReliabilityTestCount": reliability.get("count"),
    }

    def block(code: str, actual: Any, required: Any) -> None:
        blockers.append({"code": code, "actual": actual, "required": required})

    if not dual_model.get("ok"):
        block("CHALLENGER_NOT_TRAINED", dual_model.get("status"), "trained dual model")
    if clean_count < MIN_CLEAN_OFFICIAL:
        block("INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE", clean_count, MIN_CLEAN_OFFICIAL)
    if test_count < MIN_UNTOUCHED_TEST:
        block("INSUFFICIENT_UNTOUCHED_TEST", test_count, MIN_UNTOUCHED_TEST)
    if playable_evidence_count < MIN_PLAYABLE_EVIDENCE:
        block("INSUFFICIENT_PLAYABLE_EVIDENCE", playable_evidence_count, MIN_PLAYABLE_EVIDENCE)
    if outcome.get("accuracyLiftPctPoints") is None or _f(outcome.get("accuracyLiftPctPoints"), -999.0) < MIN_TEST_ACCURACY_LIFT_PCT:
        block("DOES_NOT_BEAT_MARKET_ACCURACY", outcome.get("accuracyLiftPctPoints"), MIN_TEST_ACCURACY_LIFT_PCT)
    if outcome.get("brierSkillPct") is None or _f(outcome.get("brierSkillPct"), -999.0) < MIN_BRIER_SKILL_PCT:
        block("NO_POSITIVE_BRIER_SKILL", outcome.get("brierSkillPct"), MIN_BRIER_SKILL_PCT)
    if outcome.get("logLoss") is None or baseline.get("logLoss") is None or _f(outcome.get("logLoss"), 999.0) >= _f(baseline.get("logLoss"), 0.0):
        block("LOG_LOSS_NOT_BETTER_THAN_MARKET", outcome.get("logLoss"), f"less than {baseline.get('logLoss')}")
    if outcome.get("calibrationError") is None or _f(outcome.get("calibrationError"), 999.0) > MAX_CALIBRATION_ERROR:
        block("CALIBRATION_ERROR_TOO_HIGH", outcome.get("calibrationError"), MAX_CALIBRATION_ERROR)
    if int(reliability.get("count") or 0) < 30:
        block("INSUFFICIENT_SELECTED_RELIABILITY_TEST_ROWS", reliability.get("count"), 30)

    eligible = not blockers
    return {
        "ok": True,
        "version": VERSION,
        "evaluatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "promotionEligible": eligible,
        "automaticPromotionEnabled": AUTO_PROMOTE,
        "promotionDecision": "PROMOTE" if eligible and AUTO_PROMOTE else "READY_FOR_REVIEW" if eligible else "RETAIN_CURRENT_CHAMPION",
        "checks": checks,
        "blockers": blockers,
        "directionAuthorityEnabled": bool(eligible and AUTO_PROMOTE),
        "playabilityAuthorityEnabled": bool(eligible and AUTO_PROMOTE),
        "policy": "A challenger cannot become production champion unless it beats the market on untouched chronological data and has sufficient clean official and playable evidence.",
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
    latest = history.ddb_safe({
        "PK": "MLB_ML_OPTIMIZATION#V3",
        "SK": "CHALLENGER#LATEST",
        "record_type": "mlb_ml_optimization_challenger_latest",
        "sport": "mlb",
        "created_at": created,
        "data": bundle,
    })
    dated = history.ddb_safe({
        "PK": "MLB_ML_OPTIMIZATION#V3",
        "SK": f"CHALLENGER#{created}",
        "record_type": "mlb_ml_optimization_challenger_run",
        "sport": "mlb",
        "created_at": created,
        "data": bundle,
    })
    table.put_item(Item=latest)
    table.put_item(Item=dated)
    return {"ok": True, "latestPk": latest["PK"], "latestSk": latest["SK"], "runSk": dated["SK"]}


def promote_if_allowed(bundle: Dict[str, Any]) -> Dict[str, Any]:
    decision = bundle.get("promotionGate") or {}
    if decision.get("promotionDecision") != "PROMOTE":
        return {"ok": True, "promoted": False, "reason": decision.get("promotionDecision") or "not_eligible"}
    table, history = _ddb_table()
    if table is None or history is None:
        return {"ok": False, "promoted": False, "error": "SNAPSHOTS_TABLE not configured"}
    champion = history.ddb_safe({
        "PK": "MLB_ML_OPTIMIZATION#V3",
        "SK": "CHAMPION",
        "record_type": "mlb_ml_optimization_champion",
        "sport": "mlb",
        "created_at": bundle.get("createdAtUtc"),
        "data": bundle,
    })
    table.put_item(Item=champion)
    return {"ok": True, "promoted": True, "pk": champion["PK"], "sk": champion["SK"]}


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
