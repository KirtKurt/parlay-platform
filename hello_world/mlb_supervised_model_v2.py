"""Deterministic supervised MLB shadow challenger.

The model learns a regularized residual around the de-vigged market log-odds,
selects feature groups and regularization only inside expanding chronological
folds, fits probability calibration from out-of-fold development predictions,
and evaluates the outer untouched block once. It never writes production
prediction, champion, cutover, or wagering authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import mlb_supervised_features_v2 as features

VERSION = "MLB-SUPERVISED-SHADOW-v2-residual-logistic-nested-chronological-calibrated"
DAILY_TARGET = 0.80
PROBABILITY_FLOOR = 0.05
PROBABILITY_CEILING = 0.95


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sigmoid(value: float) -> float:
    value = _clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + math.exp(-value))


def _logit(probability: float) -> float:
    probability = _clip(float(probability), 1e-6, 1.0 - 1e-6)
    return math.log(probability / (1.0 - probability))


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _days(examples: Sequence[features.Example]) -> List[str]:
    return sorted({row.day for row in examples})


def _subset(examples: Sequence[features.Example], days: Iterable[str]) -> List[features.Example]:
    selected = set(days)
    return [row for row in examples if row.day in selected]


def _suffix_days(
    examples: Sequence[features.Example], available: Sequence[str], minimum_games: int, minimum_days: int
) -> List[str]:
    counts: Dict[str, int] = {}
    for row in examples:
        counts[row.day] = counts.get(row.day, 0) + 1
    result: List[str] = []
    games = 0
    for day in reversed(list(available)):
        result.append(day)
        games += counts.get(day, 0)
        if games >= minimum_games and len(result) >= minimum_days:
            return sorted(result)
    raise ValueError("insufficient whole-slate dates for chronological partition")


def chronological_partitions(
    examples: Sequence[features.Example],
    *,
    minimum_training_games: int = 1000,
    minimum_walk_forward_games: int = 200,
    minimum_audit_games: int = 200,
    minimum_walk_forward_days: int = 20,
    minimum_audit_days: int = 15,
    explicit_audit_days: Optional[Iterable[str]] = None,
) -> Dict[str, List[str]]:
    days = _days(examples)
    counts: Dict[str, int] = {}
    for row in examples:
        counts[row.day] = counts.get(row.day, 0) + 1
    explicit = sorted({str(value) for value in explicit_audit_days or [] if str(value)})
    if explicit:
        if not set(explicit).issubset(days):
            raise ValueError("explicit audit includes unknown slate dates")
        if len(explicit) < minimum_audit_days or sum(counts[d] for d in explicit) < minimum_audit_games:
            raise ValueError("explicit audit evidence floor not met")
        before_audit = [day for day in days if day < min(explicit)]
        if any(day in set(explicit) for day in before_audit):
            raise ValueError("explicit audit overlaps development dates")
        audit = explicit
    else:
        audit = _suffix_days(examples, days, minimum_audit_games, minimum_audit_days)
        before_audit = [day for day in days if day < audit[0]]
    walk_forward = _suffix_days(
        examples, before_audit, minimum_walk_forward_games, minimum_walk_forward_days
    )
    train = [day for day in before_audit if day < walk_forward[0]]
    if sum(counts.get(day, 0) for day in train) < minimum_training_games:
        raise ValueError("training evidence floor not met")
    return {"train": train, "walkForward": walk_forward, "untouchedAudit": audit}


def inner_expanding_folds(train_days: Sequence[str], *, fold_count: int = 3) -> List[Tuple[List[str], List[str]]]:
    days = sorted(set(train_days))
    if len(days) < 45:
        raise ValueError("at least 45 training slate days are required for nested validation")
    validation_size = max(8, len(days) // 10)
    first_validation_start = len(days) - validation_size * fold_count
    if first_validation_start < 25:
        validation_size = max(5, (len(days) - 25) // fold_count)
        first_validation_start = len(days) - validation_size * fold_count
    if validation_size < 5 or first_validation_start < 20:
        raise ValueError("training date range is too small for expanding folds")
    folds: List[Tuple[List[str], List[str]]] = []
    for index in range(fold_count):
        start = first_validation_start + index * validation_size
        stop = start + validation_size
        folds.append((days[:start], days[start:stop]))
    return folds


@dataclass(frozen=True)
class Standardizer:
    names: Tuple[str, ...]
    means: Tuple[float, ...]
    scales: Tuple[float, ...]

    def transform(self, row: features.Example) -> Tuple[float, ...]:
        return tuple(
            (float(row.features.get(name, 0.0)) - mean) / scale
            for name, mean, scale in zip(self.names, self.means, self.scales)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"featureNames": list(self.names), "means": list(self.means), "scales": list(self.scales)}


def fit_standardizer(examples: Sequence[features.Example], names: Sequence[str]) -> Standardizer:
    feature_names = tuple(names)
    means: List[float] = []
    scales: List[float] = []
    for name in feature_names:
        values = [float(row.features.get(name, 0.0)) for row in examples]
        mean = sum(values) / len(values) if values else 0.0
        variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0.0
        scale = math.sqrt(variance)
        means.append(mean)
        scales.append(scale if scale >= 1e-8 else 1.0)
    return Standardizer(feature_names, tuple(means), tuple(scales))


@dataclass
class ResidualLogisticModel:
    feature_group: str
    standardizer: Standardizer
    weights: Tuple[float, ...]
    intercept: float
    l2: float
    training_steps: int
    seed: int

    def raw_probability(self, row: features.Example) -> float:
        vector = self.standardizer.transform(row)
        residual = self.intercept + sum(weight * value for weight, value in zip(self.weights, vector))
        return _clip(_sigmoid(_logit(row.market_probability) + residual), PROBABILITY_FLOOR, PROBABILITY_CEILING)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": VERSION,
            "featureGroup": self.feature_group,
            "standardizer": self.standardizer.to_dict(),
            "weights": list(self.weights),
            "intercept": self.intercept,
            "l2": self.l2,
            "trainingSteps": self.training_steps,
            "seed": self.seed,
        }


def fit_residual_logistic(
    examples: Sequence[features.Example],
    *,
    feature_group: str,
    l2: float,
    seed: int,
    steps: int = 350,
    batch_size: int = 256,
    learning_rate: float = 0.025,
) -> ResidualLogisticModel:
    if feature_group not in features.FEATURE_GROUPS:
        raise ValueError("unknown feature group")
    if not examples:
        raise ValueError("cannot fit without examples")
    names = tuple(features.FEATURE_GROUPS[feature_group])
    standardizer = fit_standardizer(examples, names)
    vectors = [standardizer.transform(row) for row in examples]
    outcomes = [float(row.outcome) for row in examples]
    offsets = [_logit(row.market_probability) for row in examples]
    dimension = len(names)
    weights = [0.0] * dimension
    intercept = 0.0
    m = [0.0] * (dimension + 1)
    v = [0.0] * (dimension + 1)
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    indices = list(range(len(examples)))
    random.Random(seed).shuffle(indices)
    batch_size = max(16, min(batch_size, len(indices)))
    for step in range(1, max(1, steps) + 1):
        start = ((step - 1) * batch_size) % len(indices)
        batch = (indices + indices)[start : start + batch_size]
        grad = [0.0] * dimension
        grad_intercept = 0.0
        for index in batch:
            vector = vectors[index]
            probability = _sigmoid(offsets[index] + intercept + sum(w * x for w, x in zip(weights, vector)))
            error = probability - outcomes[index]
            grad_intercept += error
            for position, value in enumerate(vector):
                grad[position] += error * value
        scale = 1.0 / len(batch)
        gradients = [grad_intercept * scale] + [value * scale + l2 * weights[i] for i, value in enumerate(grad)]
        parameters = [intercept] + weights
        for position, gradient in enumerate(gradients):
            m[position] = beta1 * m[position] + (1.0 - beta1) * gradient
            v[position] = beta2 * v[position] + (1.0 - beta2) * gradient * gradient
            m_hat = m[position] / (1.0 - beta1**step)
            v_hat = v[position] / (1.0 - beta2**step)
            parameters[position] -= learning_rate * m_hat / (math.sqrt(v_hat) + epsilon)
        intercept = _clip(parameters[0], -2.5, 2.5)
        weights = [_clip(value, -3.0, 3.0) for value in parameters[1:]]
    return ResidualLogisticModel(feature_group, standardizer, tuple(weights), intercept, float(l2), int(steps), int(seed))


@dataclass(frozen=True)
class PlattCalibrator:
    slope: float = 1.0
    intercept: float = 0.0

    def apply(self, probability: float) -> float:
        return _clip(
            _sigmoid(self.slope * _logit(probability) + self.intercept),
            PROBABILITY_FLOOR,
            PROBABILITY_CEILING,
        )

    def to_dict(self) -> Dict[str, float]:
        return {"slope": self.slope, "intercept": self.intercept}


def fit_platt(predictions: Sequence[float], outcomes: Sequence[int], *, steps: int = 500) -> PlattCalibrator:
    if len(predictions) != len(outcomes) or not predictions:
        return PlattCalibrator()
    slope, intercept = 1.0, 0.0
    for step in range(1, steps + 1):
        gradient_slope = 0.0
        gradient_intercept = 0.0
        for probability, outcome in zip(predictions, outcomes):
            value = _logit(probability)
            error = _sigmoid(slope * value + intercept) - int(outcome)
            gradient_slope += error * value
            gradient_intercept += error
        rate = 0.035 / math.sqrt(step)
        slope -= rate * (gradient_slope / len(predictions) + 0.002 * (slope - 1.0))
        intercept -= rate * gradient_intercept / len(predictions)
        slope = _clip(slope, 0.20, 3.0)
        intercept = _clip(intercept, -2.0, 2.0)
    return PlattCalibrator(round(slope, 10), round(intercept, 10))


def _ece(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> Tuple[float, List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    total_error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        selected = [
            (probability, outcome)
            for probability, outcome in zip(probabilities, outcomes)
            if probability >= low and (probability < high or index == bins - 1)
        ]
        if not selected:
            continue
        confidence = sum(value[0] for value in selected) / len(selected)
        observed = sum(value[1] for value in selected) / len(selected)
        error = abs(confidence - observed)
        total_error += error * len(selected) / max(1, len(probabilities))
        rows.append({
            "lower": low,
            "upper": high,
            "count": len(selected),
            "meanProbability": round(confidence, 8),
            "observedHomeWinRate": round(observed, 8),
            "absoluteError": round(error, 8),
        })
    return round(total_error, 8), rows


def evaluate_probabilities(
    examples: Sequence[features.Example], probabilities: Sequence[float], *, daily_target: float = DAILY_TARGET
) -> Dict[str, Any]:
    if len(examples) != len(probabilities):
        raise ValueError("prediction count mismatch")
    by_day: Dict[str, List[Tuple[int, float]]] = {}
    correct = 0
    brier = 0.0
    log_loss = 0.0
    outcomes: List[int] = []
    for row, probability in zip(examples, probabilities):
        probability = _clip(float(probability), 1e-9, 1.0 - 1e-9)
        outcome = int(row.outcome)
        outcomes.append(outcome)
        prediction = 1 if probability >= 0.5 else 0
        is_correct = int(prediction == outcome)
        correct += is_correct
        brier += (probability - outcome) ** 2
        log_loss += -(outcome * math.log(probability) + (1 - outcome) * math.log(1.0 - probability))
        by_day.setdefault(row.day, []).append((is_correct, probability))
    daily: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        values = by_day[day]
        day_correct = sum(value[0] for value in values)
        accuracy = day_correct / len(values)
        daily.append({
            "slateDateEt": day,
            "gameCount": len(values),
            "correct": day_correct,
            "accuracy": round(accuracy, 8),
            "dayPassed": accuracy + 1e-12 >= daily_target,
        })
    accuracies = [row["accuracy"] for row in daily]
    ece, calibration_bins = _ece(probabilities, outcomes)
    count = len(examples)
    return {
        "gameCount": count,
        "dayCount": len(daily),
        "correct": correct,
        "overallAccuracy": round(correct / count, 8) if count else 0.0,
        "meanDailyAccuracy": round(sum(accuracies) / len(accuracies), 8) if accuracies else 0.0,
        "minimumDailyAccuracy": min(accuracies) if accuracies else 0.0,
        "dailyPassRate": round(sum(row["dayPassed"] for row in daily) / len(daily), 8) if daily else 0.0,
        "brierScore": round(brier / count, 8) if count else 1.0,
        "logLoss": round(log_loss / count, 8) if count else 1.0,
        "expectedCalibrationError": ece,
        "calibrationBins": calibration_bins,
        "daily": daily,
    }


def _predict(model: ResidualLogisticModel, calibrator: PlattCalibrator, examples: Sequence[features.Example]) -> List[float]:
    return [calibrator.apply(model.raw_probability(row)) for row in examples]


def _market_metrics(examples: Sequence[features.Example]) -> Dict[str, Any]:
    return evaluate_probabilities(examples, [row.market_probability for row in examples])


def _config_key(metrics: Mapping[str, Any], market: Mapping[str, Any]) -> Tuple[float, ...]:
    accuracy_regression = max(0.0, float(market.get("overallAccuracy") or 0.0) - float(metrics.get("overallAccuracy") or 0.0))
    return (
        float(metrics.get("logLoss") or 10.0) + accuracy_regression * 0.20,
        float(metrics.get("brierScore") or 1.0),
        float(metrics.get("expectedCalibrationError") or 1.0),
        -float(metrics.get("overallAccuracy") or 0.0),
    )


def nested_select(
    examples: Sequence[features.Example], train_days: Sequence[str], *, seed: int = 260726
) -> Dict[str, Any]:
    folds = inner_expanding_folds(train_days)
    l2_values = (0.02, 0.20)
    candidates: List[Dict[str, Any]] = []
    for group_index, group in enumerate(features.FEATURE_GROUPS):
        for l2_index, l2 in enumerate(l2_values):
            fold_rows: List[Dict[str, Any]] = []
            all_probabilities: List[float] = []
            all_outcomes: List[int] = []
            all_examples: List[features.Example] = []
            for fold_index, (inner_train_days, validation_days) in enumerate(folds):
                inner_train = _subset(examples, inner_train_days)
                validation = _subset(examples, validation_days)
                model = fit_residual_logistic(
                    inner_train,
                    feature_group=group,
                    l2=l2,
                    seed=seed + group_index * 1000 + l2_index * 100 + fold_index,
                    steps=220,
                )
                probabilities = [model.raw_probability(row) for row in validation]
                metrics = evaluate_probabilities(validation, probabilities)
                market = _market_metrics(validation)
                fold_rows.append({
                    "fold": fold_index + 1,
                    "trainFirstDate": min(inner_train_days),
                    "trainLastDate": max(inner_train_days),
                    "validationFirstDate": min(validation_days),
                    "validationLastDate": max(validation_days),
                    "metrics": metrics,
                    "marketBaseline": market,
                })
                all_probabilities.extend(probabilities)
                all_outcomes.extend(row.outcome for row in validation)
                all_examples.extend(validation)
            aggregate = evaluate_probabilities(all_examples, all_probabilities)
            market_aggregate = _market_metrics(all_examples)
            candidates.append({
                "featureGroup": group,
                "l2": l2,
                "folds": fold_rows,
                "oofMetrics": aggregate,
                "oofMarketBaseline": market_aggregate,
                "oofProbabilities": all_probabilities,
                "oofOutcomes": all_outcomes,
                "selectionKey": _config_key(aggregate, market_aggregate),
            })
    selected = min(candidates, key=lambda row: tuple(row["selectionKey"]))
    ablations: Dict[str, Any] = {}
    for group in features.FEATURE_GROUPS:
        row = min((item for item in candidates if item["featureGroup"] == group), key=lambda item: tuple(item["selectionKey"]))
        ablations[group] = {
            "l2": row["l2"],
            "oofMetrics": row["oofMetrics"],
            "oofMarketBaseline": row["oofMarketBaseline"],
            "folds": row["folds"],
        }
    return {
        "selectedFeatureGroup": selected["featureGroup"],
        "selectedL2": selected["l2"],
        "selectedOofMetrics": selected["oofMetrics"],
        "selectedOofMarketBaseline": selected["oofMarketBaseline"],
        "selectedOofProbabilities": selected["oofProbabilities"],
        "selectedOofOutcomes": selected["oofOutcomes"],
        "ablation": ablations,
        "candidateCount": len(candidates),
        "foldCount": len(folds),
        "selectionUsedUntouchedAudit": False,
    }


def train_and_evaluate(
    records: Sequence[Mapping[str, Any]],
    *,
    explicit_audit_days: Optional[Iterable[str]] = None,
    seed: int = 260726,
) -> Dict[str, Any]:
    examples = features.prepare_examples(records)
    partitions = chronological_partitions(examples, explicit_audit_days=explicit_audit_days)
    train = _subset(examples, partitions["train"])
    walk_forward = _subset(examples, partitions["walkForward"])
    audit = _subset(examples, partitions["untouchedAudit"])
    selection = nested_select(examples, partitions["train"], seed=seed)
    calibrator = fit_platt(selection["selectedOofProbabilities"], selection["selectedOofOutcomes"])
    model = fit_residual_logistic(
        train,
        feature_group=selection["selectedFeatureGroup"],
        l2=float(selection["selectedL2"]),
        seed=seed + 9000,
        steps=700,
    )
    train_metrics = evaluate_probabilities(train, _predict(model, calibrator, train))
    walk_metrics = evaluate_probabilities(walk_forward, _predict(model, calibrator, walk_forward))
    audit_metrics = evaluate_probabilities(audit, _predict(model, calibrator, audit))
    market = {
        "train": _market_metrics(train),
        "walkForward": _market_metrics(walk_forward),
        "untouchedAudit": _market_metrics(audit),
    }
    v8_coverage = sum(row.features.get("v8_available", 0.0) > 0.5 for row in examples) / len(examples)
    v8_f5_coverage = sum(row.features.get("v8_f5_available", 0.0) > 0.5 for row in examples) / len(examples)
    fundamentals_coverage = sum(row.features.get("fundamentals_available", 0.0) > 0.5 for row in examples) / len(examples)
    gate_errors: List[str] = []
    for name, metrics in (("walk_forward", walk_metrics), ("untouched_audit", audit_metrics)):
        if metrics["dailyPassRate"] < 1.0 - 1e-12:
            gate_errors.append(f"{name}_contains_day_below_80_percent")
        if metrics["meanDailyAccuracy"] < DAILY_TARGET - 1e-12:
            gate_errors.append(f"{name}_mean_daily_accuracy_below_80_percent")
        if metrics["minimumDailyAccuracy"] < DAILY_TARGET - 1e-12:
            gate_errors.append(f"{name}_minimum_daily_accuracy_below_80_percent")
        if metrics["expectedCalibrationError"] > 0.08 + 1e-12:
            gate_errors.append(f"{name}_calibration_error_above_0_08")
    if walk_metrics["brierScore"] > market["walkForward"]["brierScore"] + 1e-12:
        gate_errors.append("walk_forward_brier_worse_than_market")
    if walk_metrics["logLoss"] > market["walkForward"]["logLoss"] + 1e-12:
        gate_errors.append("walk_forward_log_loss_worse_than_market")
    if audit_metrics["brierScore"] > market["untouchedAudit"]["brierScore"] + 1e-12:
        gate_errors.append("untouched_audit_brier_worse_than_market")
    if audit_metrics["logLoss"] > market["untouchedAudit"]["logLoss"] + 1e-12:
        gate_errors.append("untouched_audit_log_loss_worse_than_market")
    if len(train) < 1000 or len(walk_forward) < 200 or len(audit) < 200:
        gate_errors.append("evidence_game_floor_not_met")
    model_payload = model.to_dict()
    model_payload["calibrator"] = calibrator.to_dict()
    model_payload["featureCompilerVersion"] = features.VERSION
    model_payload["modelDigest"] = _sha(model_payload)
    result = {
        "ok": True,
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "architecture": {
            "modelType": "regularized_logistic_residual_over_market_prior",
            "marketIsOffsetNotSoleDirectionAuthority": True,
            "nestedChronologicalSelection": True,
            "wholeSlateDateIsolation": True,
            "sameDayOutcomeLeakagePrevented": True,
            "calibrationSource": "out_of_fold_development_predictions_only",
            "untouchedAuditUsedForSelection": False,
            "probabilityBounds": [PROBABILITY_FLOOR, PROBABILITY_CEILING],
        },
        "partitions": {
            name: {
                "dates": values,
                "dayCount": len(values),
                "gameCount": len(_subset(examples, values)),
                "firstDate": min(values),
                "lastDate": max(values),
            }
            for name, values in partitions.items()
        },
        "selection": {
            key: value
            for key, value in selection.items()
            if key not in {"selectedOofProbabilities", "selectedOofOutcomes"}
        },
        "model": model_payload,
        "metrics": {
            "train": train_metrics,
            "walkForward": walk_metrics,
            "untouchedAudit": audit_metrics,
            "marketBaseline": market,
        },
        "featureCoverage": {
            "exampleCount": len(examples),
            "v8Any": round(v8_coverage, 8),
            "v8FirstFive": round(v8_f5_coverage, 8),
            "frozenFundamentals": round(fundamentals_coverage, 8),
            "strictlyPastTeamHistory": round(sum(row.features.get("team_history_available", 0.0) > 0.5 for row in examples) / len(examples), 8),
        },
        "promotionGate": {
            "version": "MLB-SUPERVISED-SHADOW-PROMOTION-GATE-v1",
            "passed": not gate_errors,
            "dailyAccuracyRequirement": DAILY_TARGET,
            "calibrationErrorMaximum": 0.08,
            "errors": gate_errors,
        },
        # This first run is a retrospective architecture evaluation. Even a gate
        # pass cannot change authority; a new versioned prospective audit that
        # begins after architecture freeze is mandatory.
        "retrospectiveArchitectureEvaluation": True,
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
    }
    result["resultDigest"] = _sha(result)
    return result
