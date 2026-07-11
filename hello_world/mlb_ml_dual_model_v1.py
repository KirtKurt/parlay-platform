from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import mlb_ml_walk_forward_v1 as walk_forward

VERSION = "MLB-ML-DUAL-MODEL-v1.1-outcome-reliability-untouched-roi"
OUTCOME_MODEL_VERSION = "MLB-OUTCOME-MODEL-v1-home-win-probability"
RELIABILITY_MODEL_VERSION = "MLB-RELIABILITY-MODEL-v1.1-selected-pick-correctness-priced"
VALIDATION_PROTOCOL = "chronological_train_validation_untouched_test"

OUTCOME_FEATURES = [
    "homeMarketProb", "marketGapHome", "homeDelta", "awayDelta", "deltaGapHome",
    "homeBookDivergence", "awayBookDivergence", "homeReversalCount", "awayReversalCount",
    "homeRunLineMove", "awayRunLineMove", "homeBookAgreement", "awayBookAgreement",
    "homeSteam", "awaySteam", "homeResistance", "awayResistance",
    "homePriceImpliedProb", "awayPriceImpliedProb", "fundamentalsCompleteness",
    "homeStarterFip", "awayStarterFip", "homeStarterXfip", "awayStarterXfip",
    "homeWrcPlus", "awayWrcPlus", "homeBullpenFatigue", "awayBullpenFatigue",
    "homeLineupStrengthDelta", "awayLineupStrengthDelta", "parkFactorRuns", "windOutMph",
    "homeRestDays", "awayRestDays",
]

RELIABILITY_FEATURES = [
    "selectedMarketProb", "selectedMarketEdge", "selectedScore", "selectedReversalCount",
    "selectedBookDivergence", "selectedDelta", "selectedFavorite", "selectedHome",
    "fundamentalsCompleteness",
]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _sigmoid(value: float) -> float:
    if value >= 35:
        return 1.0
    if value <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def _american_decimal(value: Any) -> Optional[float]:
    price = _f(value, 0.0)
    if price == 0.0:
        return None
    return 1.0 + (100.0 / abs(price)) if price < 0 else 1.0 + (price / 100.0)


def records_from_clean_rows(clean_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in clean_rows or []:
        snapshot = item.get("featureSnapshot") or {}
        features = snapshot.get("features") or {}
        labels = snapshot.get("labels") or {}
        if labels.get("homeWon") not in {0, 1} or labels.get("pickCorrect") not in {0, 1}:
            continue
        record = {
            "gameId": item.get("gameId"), "slateDateEt": item.get("slateDateEt"),
            "commenceTime": item.get("commenceTime"), "homeTeam": item.get("homeTeam"),
            "awayTeam": item.get("awayTeam"), "predictedSide": item.get("predictedSide"),
            "homeWon": int(labels.get("homeWon")), "pickCorrect": int(labels.get("pickCorrect")),
            "marketHomeProbability": _f(features.get("homeMarketProb"), 0.5),
            "lockedAmericanOdds": item.get("lockedAmericanOdds"),
        }
        for feature in sorted(set(OUTCOME_FEATURES + RELIABILITY_FEATURES)):
            record[feature] = _f(features.get(feature), 0.0)
        records.append(record)
    return sorted(records, key=lambda row: str(row.get("commenceTime") or ""))


def _standardize(records: Sequence[Dict[str, Any]], features: Sequence[str]) -> Tuple[Dict[str, float], Dict[str, float]]:
    means: Dict[str, float] = {}
    scales: Dict[str, float] = {}
    for feature in features:
        values = [_f(record.get(feature)) for record in records]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
        means[feature] = mean
        scales[feature] = math.sqrt(variance) or 1.0
    return means, scales


def fit_logistic(records: Sequence[Dict[str, Any]], features: Sequence[str], target: str, version: str,
                 epochs: int = 320, learning_rate: float = 0.035, l2: float = 0.02) -> Dict[str, Any]:
    rows = list(records or [])
    if not rows:
        return {"ok": False, "version": version, "reason": "no_training_rows"}
    positives = sum(int(row.get(target) or 0) for row in rows)
    negatives = len(rows) - positives
    if not positives or not negatives:
        return {"ok": False, "version": version, "reason": "target_has_single_class", "rowCount": len(rows), "positives": positives, "negatives": negatives}
    means, scales = _standardize(rows, features)
    weights = {feature: 0.0 for feature in features}
    bias = math.log((positives + 1.0) / (negatives + 1.0))
    for epoch in range(epochs):
        grad_bias = 0.0
        grad = {feature: 0.0 for feature in features}
        for row in rows:
            z = bias
            normalized: Dict[str, float] = {}
            for feature in features:
                value = (_f(row.get(feature)) - means[feature]) / scales[feature]
                normalized[feature] = value
                z += weights[feature] * value
            probability = _sigmoid(z)
            error = probability - int(row.get(target) or 0)
            grad_bias += error
            for feature in features:
                grad[feature] += error * normalized[feature]
        rate = learning_rate / (1.0 + epoch / 500.0)
        bias -= rate * grad_bias / len(rows)
        for feature in features:
            weights[feature] -= rate * (grad[feature] / len(rows) + l2 * weights[feature])
    return {
        "ok": True, "version": version, "rowCount": len(rows), "positiveCount": positives,
        "negativeCount": negatives, "target": target, "features": list(features), "bias": bias,
        "weights": weights, "means": means, "scales": scales,
        "training": {"epochs": epochs, "learningRate": learning_rate, "l2": l2},
        "validationProtocol": VALIDATION_PROTOCOL,
    }


def score(record: Dict[str, Any], model: Dict[str, Any]) -> float:
    if not model or not model.get("ok"):
        return 0.5
    z = _f(model.get("bias"))
    means = model.get("means") or {}; scales = model.get("scales") or {}; weights = model.get("weights") or {}
    for feature in model.get("features") or []:
        scale = _f(scales.get(feature), 1.0) or 1.0
        z += _f(weights.get(feature)) * ((_f(record.get(feature)) - _f(means.get(feature))) / scale)
    return _sigmoid(z)


def _selected_reliability_test(rows: Sequence[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    selected = [row for row in rows if walk_forward.clip_probability(row.get("reliabilityProbability")) >= threshold]
    priced = []
    profit = 0.0
    for row in selected:
        decimal = _american_decimal(row.get("lockedAmericanOdds"))
        if decimal is None:
            continue
        priced.append(row)
        profit += (decimal - 1.0) if int(row.get("pickCorrect") or 0) == 1 else -1.0
    return {
        "count": len(selected),
        "correct": sum(int(row.get("pickCorrect") or 0) for row in selected),
        "coveragePct": round(len(selected) / len(rows) * 100.0, 2) if rows else 0.0,
        "accuracyPct": walk_forward.accuracy(selected, "reliabilityProbability", "pickCorrect", threshold=threshold),
        "brierScore": walk_forward.brier(selected, "reliabilityProbability", "pickCorrect"),
        "logLoss": walk_forward.log_loss(selected, "reliabilityProbability", "pickCorrect"),
        "calibrationError": walk_forward.calibration_error(selected, "reliabilityProbability", "pickCorrect"),
        "threshold": threshold,
        "pricedCount": len(priced),
        "unpricedCount": len(selected) - len(priced),
        "priceCoveragePct": round(len(priced) / len(selected) * 100.0, 2) if selected else 0.0,
        "flatUnitProfit": round(profit, 4) if priced else None,
        "flatUnitRoiPct": round(profit / len(priced) * 100.0, 2) if priced else None,
    }


def _data_quality(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    completeness = [_f(row.get("fundamentalsCompleteness"), 0.0) for row in records]
    priced = sum(_american_decimal(row.get("lockedAmericanOdds")) is not None for row in records)
    return {
        "recordCount": len(records),
        "averageFundamentalsCompletenessPct": round(sum(completeness) / len(completeness) * 100.0, 2) if completeness else 0.0,
        "rowsWithAtLeast75PctFundamentals": sum(value >= 0.75 for value in completeness),
        "priceCoveragePct": round(priced / len(records) * 100.0, 2) if records else 0.0,
        "modelScope": "FULL_BASEBALL_CONTEXT" if completeness and sum(value >= 0.75 for value in completeness) / len(completeness) >= 0.90 else "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
    }


def train(clean_rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    records = records_from_clean_rows(clean_rows)
    split = walk_forward.split_chronological(records)
    if not split.get("ok"):
        return {
            "ok": False, "version": VERSION, "recordCount": len(records),
            "split": {key: value for key, value in split.items() if key not in {"train", "validation", "test"}},
            "outcomeModel": {"ok": False, "reason": "insufficient_clean_rows"},
            "reliabilityModel": {"ok": False, "reason": "insufficient_clean_rows"},
            "status": "ACCUMULATING_CLEAN_POST_FIX_EVIDENCE", "dataQuality": _data_quality(records),
        }
    train_rows = split["train"]; validation_rows = split["validation"]; test_rows = split["test"]
    outcome_model = fit_logistic(train_rows, OUTCOME_FEATURES, "homeWon", OUTCOME_MODEL_VERSION)
    reliability_model = fit_logistic(train_rows, RELIABILITY_FEATURES, "pickCorrect", RELIABILITY_MODEL_VERSION)
    if not outcome_model.get("ok") or not reliability_model.get("ok"):
        return {"ok": False, "version": VERSION, "recordCount": len(records), "outcomeModel": outcome_model,
                "reliabilityModel": reliability_model, "status": "TRAINING_BLOCKED", "dataQuality": _data_quality(records)}
    validation_scored = [{**row, "outcomeProbability": score(row, outcome_model), "reliabilityProbability": score(row, reliability_model)} for row in validation_rows]
    threshold = walk_forward.select_reliability_threshold(validation_scored)
    selected_threshold = _f(threshold.get("threshold"), 0.70) if threshold.get("ok") else 0.70
    test_scored = [{**row, "outcomeProbability": score(row, outcome_model), "reliabilityProbability": score(row, reliability_model)} for row in test_rows]
    outcome_validation = walk_forward.evaluate(validation_scored, "outcomeProbability", "homeWon", baseline_probability_key="marketHomeProbability")
    outcome_test = walk_forward.evaluate(test_scored, "outcomeProbability", "homeWon", baseline_probability_key="marketHomeProbability")
    reliability_validation = walk_forward.evaluate(validation_scored, "reliabilityProbability", "pickCorrect")
    reliability_test = walk_forward.evaluate(test_scored, "reliabilityProbability", "pickCorrect")
    selected_test = _selected_reliability_test(test_scored, selected_threshold)
    reliability_model["selectedThreshold"] = {**threshold, "threshold": selected_threshold}
    reliability_model["thresholdSelectedOnValidationOnly"] = True
    outcome_model["directionThreshold"] = 0.5
    return {
        "ok": True, "version": VERSION, "recordCount": len(records),
        "split": {key: value for key, value in split.items() if key not in {"train", "validation", "test"}},
        "outcomeModel": outcome_model, "reliabilityModel": reliability_model,
        "validation": {"outcome": outcome_validation, "reliability": reliability_validation, "selectedReliability": threshold},
        "untouchedTest": {"outcome": outcome_test, "reliability": reliability_test, "selectedReliability": selected_test},
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "status": "CHALLENGER_TRAINED_AWAITING_SEPARATE_PROMOTION_GATES",
        "dataQuality": _data_quality(records),
        "policy": "Outcome predicts home-team win probability. Reliability predicts selected-pick correctness. Threshold selection uses validation only; accuracy, calibration, price coverage, and ROI are evaluated only on untouched test rows.",
    }
