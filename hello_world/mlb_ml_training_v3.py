from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from mlb_ml_frozen_features import OUTCOME_FEATURES, RELIABILITY_FEATURES, VERSION as FREEZE_VERSION

VERSION = "MLB-ML-TRAINING-v3-clean-dual-model-chronological-champion-challenger"
VALIDATION_PROTOCOL = "chronological_train_validation_test_v1"
OUTCOME_CHALLENGER_PATH = "runtime_reports/mlb_ml_outcome_challenger_latest.json"
RELIABILITY_CHALLENGER_PATH = "runtime_reports/mlb_ml_reliability_challenger_latest.json"
OUTCOME_CHAMPION_PATH = "runtime_reports/mlb_ml_outcome_champion.json"
RELIABILITY_CHAMPION_PATH = "runtime_reports/mlb_ml_model_latest.json"
MANIFEST_PATH = "runtime_reports/mlb_ml_training_manifest_latest.json"
OUTCOME_DATASET_PATH = "runtime_reports/mlb_ml_clean_outcome_dataset_latest.csv"
RELIABILITY_DATASET_PATH = "runtime_reports/mlb_ml_clean_reliability_dataset_latest.csv"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _american_decimal(price: Any) -> Optional[float]:
    value = _f(price, 0.0)
    if value == 0.0:
        return None
    return 1.0 + (100.0 / abs(value)) if value < 0 else 1.0 + (value / 100.0)


def _selected_price(row: Dict[str, Any]) -> Optional[float]:
    side = str(row.get("predictedSide") or "").lower()
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    signal = signal if isinstance(signal, dict) else {}
    for value in (signal.get("americanOdds"), signal.get("averageAmericanOdds"), row.get("lockedAmericanOdds"), row.get("americanOdds")):
        if value not in (None, ""):
            return _f(value)
    return None


def _eligibility(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    freeze = row.get("mlFeatureFreeze") or {}
    audit = row.get("lockedCardAudit") or {}
    if row.get("status") != "GRADED" or row.get("correct") not in {True, False}:
        reasons.append("not_graded")
    if freeze.get("applied") is not True or freeze.get("version") != FREEZE_VERSION:
        reasons.append("missing_current_frozen_feature_vector")
    if freeze.get("immutable") is not True:
        reasons.append("feature_vector_not_immutable")
    if freeze.get("trainingEligible") is not True:
        reasons.extend([str(x) for x in (freeze.get("trainingExclusionReasons") or ["freeze_marked_ineligible"])])
    if audit.get("lockedFlag") is not True or audit.get("preventsLateRows") is not True:
        reasons.append("invalid_locked_card_audit")
    if not (row.get("frozenOutcomeFeatures") and row.get("frozenReliabilityFeatures")):
        reasons.append("missing_frozen_features")
    if not (row.get("id") or audit.get("providerGameId")):
        reasons.append("missing_provider_game_id")
    return not reasons, sorted(set(reasons))


def clean_training_rows(rows: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    exclusions: Counter[str] = Counter()
    total = 0
    for row in rows or []:
        total += 1
        eligible, reasons = _eligibility(row)
        if not eligible:
            exclusions.update(reasons)
            continue
        provider_id = str(row.get("id") or (row.get("lockedCardAudit") or {}).get("providerGameId") or "")
        key = f"{row.get('slateDateEt')}|{provider_id}|{row.get('commenceTime')}"
        if key not in seen:
            seen[key] = row
    clean = sorted(seen.values(), key=lambda r: str(r.get("commenceTime") or ""))
    return clean, {
        "inputRows": total,
        "eligibleRows": len(clean),
        "quarantinedRows": total - len(clean),
        "exclusionReasons": dict(exclusions),
        "cleanCohortPolicy": "Only current-version immutable lock-time feature vectors are eligible. Legacy and regenerated settled features are quarantined.",
    }


def outcome_records(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = []
    for row in rows:
        features = dict(row.get("frozenOutcomeFeatures") or {})
        label = 1 if _norm(row.get("winner")) == _norm(row.get("homeTeam")) else 0
        records.append({
            "slateDate": row.get("slateDateEt"), "commenceTime": row.get("commenceTime"),
            "gameId": row.get("id") or (row.get("lockedCardAudit") or {}).get("providerGameId"),
            "homeTeam": row.get("homeTeam"), "awayTeam": row.get("awayTeam"),
            "winner": row.get("winner"), "label": label,
            "marketHomeProb": _f(features.get("homeMarketProb"), 0.5),
            **{name: _f(features.get(name)) for name in OUTCOME_FEATURES},
        })
    return records


def reliability_records(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = []
    for row in rows:
        features = dict(row.get("frozenReliabilityFeatures") or {})
        records.append({
            "slateDate": row.get("slateDateEt"), "commenceTime": row.get("commenceTime"),
            "gameId": row.get("id") or (row.get("lockedCardAudit") or {}).get("providerGameId"),
            "predictedWinner": row.get("predictedWinner"), "winner": row.get("winner"),
            "label": 1 if row.get("correct") is True else 0,
            "americanOdds": _selected_price(row),
            **{name: _f(features.get(name)) for name in RELIABILITY_FEATURES},
        })
    return records


def chronological_split(records: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    rows = sorted(records, key=lambda r: (str(r.get("commenceTime") or ""), str(r.get("gameId") or "")))
    n = len(rows)
    train_end = max(1, int(n * 0.60))
    validation_end = max(train_end + 1, int(n * 0.80)) if n >= 3 else n
    validation_end = min(validation_end, n)
    return {"train": rows[:train_end], "validation": rows[train_end:validation_end], "test": rows[validation_end:]}


def _fit_logistic(rows: Sequence[Dict[str, Any]], features: Sequence[str]) -> Dict[str, Any]:
    if not rows:
        return {"bias": 0.0, "weights": {f: 0.0 for f in features}, "means": {f: 0.0 for f in features}, "scales": {f: 1.0 for f in features}}
    means = {f: sum(_f(r.get(f)) for r in rows) / len(rows) for f in features}
    scales: Dict[str, float] = {}
    for f in features:
        variance = sum((_f(r.get(f)) - means[f]) ** 2 for r in rows) / max(1, len(rows) - 1)
        scales[f] = math.sqrt(variance) or 1.0
    weights = {f: 0.0 for f in features}
    positives = sum(int(r.get("label") or 0) for r in rows)
    bias = math.log((positives + 1.0) / (len(rows) - positives + 1.0))
    learning_rate = 0.05
    l2 = 0.002
    for _ in range(500):
        grad_b = 0.0
        grad_w = {f: 0.0 for f in features}
        for row in rows:
            z = bias + sum(weights[f] * ((_f(row.get(f)) - means[f]) / scales[f]) for f in features)
            p = 1.0 if z >= 35 else 0.0 if z <= -35 else 1.0 / (1.0 + math.exp(-z))
            err = p - int(row.get("label") or 0)
            grad_b += err
            for f in features:
                grad_w[f] += err * ((_f(row.get(f)) - means[f]) / scales[f])
        bias -= learning_rate * grad_b / len(rows)
        for f in features:
            weights[f] -= learning_rate * ((grad_w[f] / len(rows)) + l2 * weights[f])
    return {"bias": bias, "weights": {f: round(weights[f], 8) for f in features}, "means": means, "scales": scales}


def _predict(row: Dict[str, Any], model: Dict[str, Any], features: Sequence[str]) -> float:
    z = _f(model.get("bias"))
    weights = model.get("weights") or {}
    means = model.get("means") or {}
    scales = model.get("scales") or {}
    for f in features:
        scale = _f(scales.get(f), 1.0) or 1.0
        z += _f(weights.get(f)) * ((_f(row.get(f)) - _f(means.get(f))) / scale)
    return 1.0 if z >= 35 else 0.0 if z <= -35 else 1.0 / (1.0 + math.exp(-z))


def _classification_metrics(rows: Sequence[Dict[str, Any]], model: Dict[str, Any], features: Sequence[str], threshold: float = 0.5) -> Dict[str, Any]:
    if not rows:
        return {"count": 0, "accuracyPct": None, "brierScore": None, "logLoss": None}
    probabilities = [_predict(row, model, features) for row in rows]
    labels = [int(row.get("label") or 0) for row in rows]
    correct = sum((p >= threshold) == bool(y) for p, y in zip(probabilities, labels))
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, labels)) / len(rows)
    logloss = -sum(y * math.log(max(p, 1e-9)) + (1-y) * math.log(max(1-p, 1e-9)) for p, y in zip(probabilities, labels)) / len(rows)
    return {"count": len(rows), "correct": correct, "wrong": len(rows)-correct, "accuracyPct": round(correct/len(rows)*100.0,2), "brierScore": round(brier,6), "logLoss": round(logloss,6)}


def _market_baseline_metrics(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"count": 0, "accuracyPct": None, "brierScore": None, "logLoss": None}
    probs = [min(0.999, max(0.001, _f(r.get("marketHomeProb"), 0.5))) for r in rows]
    labels = [int(r.get("label") or 0) for r in rows]
    correct = sum((p >= 0.5) == bool(y) for p, y in zip(probs, labels))
    brier = sum((p-y) ** 2 for p, y in zip(probs, labels)) / len(rows)
    logloss = -sum(y*math.log(p)+(1-y)*math.log(1-p) for p,y in zip(probs,labels))/len(rows)
    return {"count":len(rows),"accuracyPct":round(correct/len(rows)*100.0,2),"brierScore":round(brier,6),"logLoss":round(logloss,6)}


def _tune_reliability_threshold(rows: Sequence[Dict[str, Any]], model: Dict[str, Any]) -> Dict[str, Any]:
    min_selected = int(os.environ.get("INQSI_MLB_ML_MIN_VALIDATION_SELECTED", "20"))
    target = float(os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY", "60"))
    candidates = []
    for i in range(50, 96):
        threshold = i / 100.0
        selected = [(row, _predict(row, model, RELIABILITY_FEATURES)) for row in rows]
        selected = [(row,p) for row,p in selected if p >= threshold]
        correct = sum(int(row.get("label") or 0) for row,_ in selected)
        accuracy = correct/len(selected)*100.0 if selected else None
        candidates.append({"threshold":threshold,"selectedCount":len(selected),"correct":correct,"accuracyPct":round(accuracy,2) if accuracy is not None else None})
    eligible = [c for c in candidates if c["selectedCount"] >= min_selected and (c["accuracyPct"] or 0.0) >= target]
    chosen = max(eligible, key=lambda c:(c["selectedCount"],c["accuracyPct"])) if eligible else None
    return {"chosen":chosen,"candidates":candidates,"minValidationSelected":min_selected,"targetAccuracyPct":target}


def _reliability_test_metrics(rows: Sequence[Dict[str, Any]], model: Dict[str, Any], threshold: Optional[float]) -> Dict[str, Any]:
    if threshold is None or not rows:
        return {"testCount":len(rows),"selectedCount":0,"selectedAccuracyPct":None,"selectedFlatUnitRoiPct":None,"pricedSelectedCount":0}
    selected = [(row,_predict(row,model,RELIABILITY_FEATURES)) for row in rows]
    selected = [(row,p) for row,p in selected if p >= threshold]
    correct = sum(int(row.get("label") or 0) for row,_ in selected)
    profit = 0.0
    priced = 0
    for row,_ in selected:
        decimal = _american_decimal(row.get("americanOdds"))
        if decimal is None:
            continue
        priced += 1
        profit += (decimal-1.0) if int(row.get("label") or 0)==1 else -1.0
    return {
        "testCount":len(rows),"selectedCount":len(selected),"selectedCorrect":correct,
        "selectedAccuracyPct":round(correct/len(selected)*100.0,2) if selected else None,
        "pricedSelectedCount":priced,"priceCoveragePct":round(priced/len(selected)*100.0,2) if selected else 0.0,
        "selectedFlatUnitProfit":round(profit,4) if priced else None,
        "selectedFlatUnitRoiPct":round(profit/priced*100.0,2) if priced else None,
    }


def _write_csv(path: str, records: Sequence[Dict[str, Any]], features: Sequence[str], extra: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    columns = [*extra,*features,"label"]
    with open(path,"w",newline="",encoding="utf-8") as fh:
        writer=csv.DictWriter(fh,fieldnames=columns); writer.writeheader()
        for record in records:
            writer.writerow({key:record.get(key,"") for key in columns})


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,"w",encoding="utf-8") as fh:
        json.dump(payload,fh,indent=2,default=str); fh.write("\n")


def _approved_champion_or_placeholder(path: str, role: str) -> Dict[str, Any]:
    try:
        with open(path,encoding="utf-8") as fh:
            existing=json.load(fh)
        if existing.get("productionApproved") is True and existing.get("cleanCohort") is True:
            return existing
    except Exception:
        pass
    return {"ok":False,"modelRole":role,"productionApproved":False,"cleanCohort":True,"validationProtocol":VALIDATION_PROTOCOL,"featureFreezeRequired":True,"reason":"no_approved_clean_champion_yet","version":VERSION}


def train(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    clean_rows, cohort = clean_training_rows(rows)
    outcomes = outcome_records(clean_rows)
    reliability = reliability_records(clean_rows)
    _write_csv(OUTCOME_DATASET_PATH,outcomes,OUTCOME_FEATURES,["slateDate","commenceTime","gameId","homeTeam","awayTeam","winner","marketHomeProb"])
    _write_csv(RELIABILITY_DATASET_PATH,reliability,RELIABILITY_FEATURES,["slateDate","commenceTime","gameId","predictedWinner","winner","americanOdds"])
    minimum=int(os.environ.get("INQSI_MLB_CLEAN_MIN_TRAINING_ROWS","100"))
    outcome_split=chronological_split(outcomes); reliability_split=chronological_split(reliability)
    outcome_model=_fit_logistic(outcome_split["train"],OUTCOME_FEATURES) if len(outcomes)>=minimum else {}
    outcome_validation=_classification_metrics(outcome_split["validation"],outcome_model,OUTCOME_FEATURES) if outcome_model else {"count":len(outcome_split["validation"])}
    outcome_test=_classification_metrics(outcome_split["test"],outcome_model,OUTCOME_FEATURES) if outcome_model else {"count":len(outcome_split["test"])}
    market_test=_market_baseline_metrics(outcome_split["test"])
    outcome_recommended=bool(
        len(outcomes)>=int(os.environ.get("INQSI_MLB_OUTCOME_PROMOTION_MIN_ROWS","500"))
        and outcome_test.get("count",0)>=int(os.environ.get("INQSI_MLB_OUTCOME_MIN_TEST_ROWS","100"))
        and outcome_test.get("accuracyPct") is not None and market_test.get("accuracyPct") is not None
        and outcome_test["accuracyPct"]>market_test["accuracyPct"]
        and outcome_test.get("brierScore",9)<market_test.get("brierScore",9)
        and outcome_test.get("logLoss",9)<market_test.get("logLoss",9)
    )
    outcome_payload={
        "ok":bool(outcome_model),"version":VERSION+"+OUTCOME","modelRole":"outcome","cleanCohort":True,
        "featureFreezeRequired":True,"featureFreezeVersion":FREEZE_VERSION,"validationProtocol":VALIDATION_PROTOCOL,
        "features":OUTCOME_FEATURES,"rowCount":len(outcomes),"trainCount":len(outcome_split["train"]),
        "validationCount":len(outcome_split["validation"]),"testCount":len(outcome_split["test"]),**outcome_model,
        "validationMetrics":outcome_validation,"testMetrics":outcome_test,"marketBaselineTestMetrics":market_test,
        "promotionRecommended":outcome_recommended,"productionApproved":False,"manualChampionPromotionRequired":True,
        "promotionPolicy":"At least 500 clean rows and 100 untouched test rows; test accuracy, Brier score, and log loss must all beat the de-vigged market baseline.",
    }
    reliability_model=_fit_logistic(reliability_split["train"],RELIABILITY_FEATURES) if len(reliability)>=minimum else {}
    tuning=_tune_reliability_threshold(reliability_split["validation"],reliability_model) if reliability_model else {"chosen":None,"candidates":[]}
    threshold=(tuning.get("chosen") or {}).get("threshold")
    reliability_test=_reliability_test_metrics(reliability_split["test"],reliability_model,threshold) if reliability_model else {"testCount":len(reliability_split["test"]),"selectedCount":0}
    reliability_recommended=bool(
        len(reliability)>=int(os.environ.get("INQSI_MLB_RELIABILITY_PROMOTION_MIN_ROWS","500"))
        and reliability_test.get("testCount",0)>=int(os.environ.get("INQSI_MLB_RELIABILITY_MIN_TEST_ROWS","100"))
        and reliability_test.get("selectedCount",0)>=int(os.environ.get("INQSI_MLB_RELIABILITY_MIN_SELECTED_TEST_ROWS","50"))
        and (reliability_test.get("selectedAccuracyPct") or 0)>=float(os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY","60"))
        and (reliability_test.get("priceCoveragePct") or 0)>=90.0
        and reliability_test.get("selectedFlatUnitRoiPct") is not None and reliability_test["selectedFlatUnitRoiPct"]>0.0
    )
    reliability_payload={
        "ok":bool(reliability_model),"version":VERSION+"+RELIABILITY","modelRole":"reliability","cleanCohort":True,
        "featureFreezeRequired":True,"featureFreezeVersion":FREEZE_VERSION,"validationProtocol":VALIDATION_PROTOCOL,
        "features":RELIABILITY_FEATURES,"rowCount":len(reliability),"trainCount":len(reliability_split["train"]),
        "validationCount":len(reliability_split["validation"]),"testCount":len(reliability_split["test"]),**reliability_model,
        "validationThresholdSelection":tuning,"promotionThreshold":tuning.get("chosen"),"testMetrics":reliability_test,
        "promotionTargetAccuracyPct":float(os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY","60")),
        "promotionRecommended":reliability_recommended,"productionApproved":False,"manualChampionPromotionRequired":True,
        "promotionPolicy":"Threshold is selected on validation only. At least 500 clean rows, 100 untouched test rows, 50 selected test rows, 90% price coverage, 60% selected accuracy, and positive flat-unit ROI are required.",
    }
    _write_json(OUTCOME_CHALLENGER_PATH,outcome_payload); _write_json(RELIABILITY_CHALLENGER_PATH,reliability_payload)
    _write_json(OUTCOME_CHAMPION_PATH,_approved_champion_or_placeholder(OUTCOME_CHAMPION_PATH,"outcome"))
    _write_json(RELIABILITY_CHAMPION_PATH,_approved_champion_or_placeholder(RELIABILITY_CHAMPION_PATH,"reliability"))
    advanced=[((r.get("mlFeatureFreeze") or {}).get("advancedInputs") or {}).get("coveragePct",0.0) for r in clean_rows]
    price_count=sum(_selected_price(r) is not None for r in clean_rows)
    manifest={
        "ok":True,"version":VERSION,"createdAtUtc":datetime.now(timezone.utc).isoformat(),"cohort":cohort,
        "cleanRowCount":len(clean_rows),"legacyRowsQuarantined":cohort["quarantinedRows"],
        "outcomeModel":{"path":OUTCOME_CHALLENGER_PATH,"promotionRecommended":outcome_recommended,"testMetrics":outcome_test,"marketBaseline":market_test},
        "reliabilityModel":{"path":RELIABILITY_CHALLENGER_PATH,"promotionRecommended":reliability_recommended,"testMetrics":reliability_test},
        "productionChampions":{"outcome":OUTCOME_CHAMPION_PATH,"reliability":RELIABILITY_CHAMPION_PATH},
        "advancedInputCoveragePct":round(sum(advanced)/len(advanced),2) if advanced else 0.0,
        "advancedModelClaimAllowed":bool(advanced and sum(1 for x in advanced if x>=75.0)/len(advanced)>=0.9),
        "priceCoveragePct":round(price_count/len(clean_rows)*100.0,2) if clean_rows else 0.0,
        "optimizationPolicy":"Beat the de-vigged market baseline on untouched chronological test data; 90% all-games accuracy is not a production promotion criterion.",
        "automaticPromotion":False,
    }
    _write_json(MANIFEST_PATH,manifest)
    return manifest


def patch_audit_copy_fields() -> None:
    try:
        import mlb_locked_card_audit_v1 as base
    except Exception:
        return
    if getattr(base,"_INQSI_MLB_FROZEN_FIELDS_COPIED",False):
        return
    original=base._copy_audit_fields
    def copied(pred: Dict[str, Any]) -> Dict[str, Any]:
        out=original(pred)
        for key in ("mlFeatureFreeze","frozenOutcomeFeatures","frozenReliabilityFeatures","featureVectorFrozenAtLock","predictionSemanticsVersion","teamWinProbabilityPct","mlPickReliabilityPct","americanOdds","priceBook","priceSource","modelVersion","engine"):
            out[key]=pred.get(key)
        return out
    base._copy_audit_fields=copied
    base._INQSI_MLB_FROZEN_FIELDS_COPIED=True


def apply(actionability_module: Any):
    if getattr(actionability_module,"_INQSI_MLB_TRAINING_V3_APPLIED",False):
        return actionability_module
    patch_audit_copy_fields()
    def write_artifacts(report: Dict[str, Any], audit_module: Any) -> Dict[str, Any]:
        current=report.get("rows") or []
        try:
            historical=audit_module.historical_audit_rows()
        except Exception:
            historical=[]
        manifest=train(list(current)+list(historical))
        return {
            "ok":True,"version":VERSION,"cleanRecordCount":manifest.get("cleanRowCount"),
            "legacyRowsQuarantined":manifest.get("legacyRowsQuarantined"),"manifestPath":MANIFEST_PATH,
            "outcomeChallengerPath":OUTCOME_CHALLENGER_PATH,"reliabilityChallengerPath":RELIABILITY_CHALLENGER_PATH,
            "outcomeChampionPath":OUTCOME_CHAMPION_PATH,"reliabilityChampionPath":RELIABILITY_CHAMPION_PATH,
            "promotionRecommended":{"outcome":(manifest.get("outcomeModel") or {}).get("promotionRecommended"),"reliability":(manifest.get("reliabilityModel") or {}).get("promotionRecommended")},
            "cohort":manifest.get("cohort"),
        }
    actionability_module._write_ml_artifacts=write_artifacts
    actionability_module._INQSI_MLB_TRAINING_V3_APPLIED=True
    return actionability_module
