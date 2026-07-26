"""Numerical model, calibration, chronological partitions, and metrics."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np

from mlb_supervised_features_v1 import Example, _clip, _f, _logit, _sigmoid

VERSION = "MLB-SUPERVISED-MODEL-v1.0-regularized-market-residual"
MODEL_FAMILY = "REGULARIZED_LOGISTIC_MARKET_RESIDUAL"
PROBABILITY_CAP = 0.85
MIN_PROBABILITY = 1.0 - PROBABILITY_CAP
DAILY_TARGET = 0.80


@dataclass
class LogisticModel:
    feature_names: Tuple[str, ...]
    means: Tuple[float, ...]
    scales: Tuple[float, ...]
    weights: Tuple[float, ...]
    intercept: float
    l2: float

    def probability(self, example: Example) -> float:
        linear = self.intercept + _logit(example.market_probability)
        for index, name in enumerate(self.feature_names):
            value = (example.features.get(name, 0.0) - self.means[index]) / self.scales[index]
            linear += value * self.weights[index]
        return _clip(_sigmoid(linear), MIN_PROBABILITY, PROBABILITY_CAP)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "family": MODEL_FAMILY,
            "featureNames": list(self.feature_names),
            "means": list(self.means),
            "scales": list(self.scales),
            "weights": list(self.weights),
            "intercept": self.intercept,
            "l2": self.l2,
            "probabilityCap": PROBABILITY_CAP,
        }


def fit_logistic(
    examples: Sequence[Example],
    feature_names: Sequence[str],
    l2: float,
    iterations: int = 350,
) -> LogisticModel:
    names = tuple(feature_names)
    if not examples:
        raise ValueError("cannot fit supervised model without examples")
    matrix = np.asarray(
        [[example.features.get(name, 0.0) for name in names] for example in examples],
        dtype=np.float64,
    )
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales = np.where(scales < 1e-6, 1.0, scales)
    standardized = (matrix - means) / scales
    outcomes = np.asarray([example.outcome for example in examples], dtype=np.float64)
    offsets = np.asarray([_logit(example.market_probability) for example in examples], dtype=np.float64)
    weights = np.zeros(len(names), dtype=np.float64)
    intercept = 0.0
    first_moment = np.zeros(len(names), dtype=np.float64)
    second_moment = np.zeros(len(names), dtype=np.float64)
    intercept_first = 0.0
    intercept_second = 0.0
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    learning_rate = 0.025
    best_loss = float("inf")
    stale = 0
    for step in range(1, iterations + 1):
        linear = np.clip(offsets + intercept + standardized @ weights, -35.0, 35.0)
        probabilities = 1.0 / (1.0 + np.exp(-linear))
        errors = probabilities - outcomes
        gradient_intercept = float(errors.mean())
        gradient_weights = standardized.T @ errors / len(examples) + l2 * weights
        bounded = np.clip(probabilities, 1e-9, 1.0 - 1e-9)
        loss = float(
            -(outcomes * np.log(bounded) + (1.0 - outcomes) * np.log(1.0 - bounded)).mean()
            + 0.5 * l2 * np.dot(weights, weights)
        )
        intercept_first = beta1 * intercept_first + (1.0 - beta1) * gradient_intercept
        intercept_second = beta2 * intercept_second + (1.0 - beta2) * gradient_intercept**2
        intercept -= learning_rate * (intercept_first / (1.0 - beta1**step)) / (
            math.sqrt(intercept_second / (1.0 - beta2**step)) + epsilon
        )
        first_moment = beta1 * first_moment + (1.0 - beta1) * gradient_weights
        second_moment = beta2 * second_moment + (1.0 - beta2) * (gradient_weights**2)
        weights -= learning_rate * (first_moment / (1.0 - beta1**step)) / (
            np.sqrt(second_moment / (1.0 - beta2**step)) + epsilon
        )
        if loss + 1e-8 < best_loss:
            best_loss = loss
            stale = 0
        else:
            stale += 1
            if stale >= 45 and step >= 120:
                break
    return LogisticModel(
        names,
        tuple(float(value) for value in means),
        tuple(float(value) for value in scales),
        tuple(float(value) for value in weights),
        float(intercept),
        l2,
    )


@dataclass(frozen=True)
class Calibration:
    temperature: float = 1.0
    bias: float = 0.0

    def apply(self, probability: float) -> float:
        return _clip(
            _sigmoid(_logit(probability) / self.temperature + self.bias),
            MIN_PROBABILITY,
            PROBABILITY_CAP,
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "temperature": self.temperature,
            "bias": self.bias,
            "probabilityCap": PROBABILITY_CAP,
        }


def fit_calibration(predictions: Sequence[Tuple[float, int]]) -> Calibration:
    if not predictions:
        return Calibration()
    best = (float("inf"), float("inf"), Calibration())
    for temperature in (0.70, 0.85, 1.0, 1.15, 1.35, 1.60, 2.0, 2.5):
        for bias in (-0.35, -0.25, -0.15, -0.075, 0.0, 0.075, 0.15, 0.25, 0.35):
            calibration = Calibration(temperature, bias)
            log_loss = 0.0
            brier = 0.0
            for raw, outcome in predictions:
                probability = calibration.apply(raw)
                log_loss += -(
                    outcome * math.log(probability)
                    + (1 - outcome) * math.log(1.0 - probability)
                )
                brier += (probability - outcome) ** 2
            candidate = (log_loss / len(predictions), brier / len(predictions), calibration)
            if candidate[:2] < best[:2]:
                best = candidate
    return best[2]


def _by_dates(examples: Sequence[Example], dates: Iterable[str]) -> List[Example]:
    allowed = set(dates)
    return [example for example in examples if example.day in allowed]


def chronological_partitions(
    examples: Sequence[Example],
    walk_games: int = 200,
    holdout_games: int = 200,
    walk_days: int = 20,
    holdout_days: int = 15,
) -> Dict[str, List[str]]:
    counts: Dict[str, int] = {}
    for example in examples:
        counts[example.day] = counts.get(example.day, 0) + 1
    dates = sorted(counts)

    def suffix(available: Sequence[str], minimum_games: int, minimum_days: int) -> List[str]:
        selected: List[str] = []
        total = 0
        for value in reversed(list(available)):
            selected.append(value)
            total += counts[value]
            if total >= minimum_games and len(selected) >= minimum_days:
                return sorted(selected)
        raise ValueError("insufficient chronological whole-slate evidence")

    holdout = suffix(dates, holdout_games, holdout_days)
    before_holdout = dates[: dates.index(holdout[0])]
    walk_forward = suffix(before_holdout, walk_games, walk_days)
    training = before_holdout[: before_holdout.index(walk_forward[0])]
    if sum(counts[day] for day in training) < 1000:
        raise ValueError("supervised training partition has fewer than 1000 games")
    return {
        "train": training,
        "walkForward": walk_forward,
        "untouchedHoldout": holdout,
    }


def _expanding_blocks(
    dates: Sequence[str], folds: int = 4
) -> List[Tuple[List[str], List[str]]]:
    ordered = sorted(dates)
    if len(ordered) < 24:
        split = max(1, int(len(ordered) * 0.75))
        return [(ordered[:split], ordered[split:])]
    start = max(20, int(len(ordered) * 0.50))
    remaining = len(ordered) - start
    block_size = max(1, remaining // folds)
    blocks = []
    cursor = start
    while cursor < len(ordered):
        stop = min(len(ordered), cursor + block_size)
        if len(blocks) == folds - 1:
            stop = len(ordered)
        validation = ordered[cursor:stop]
        if validation:
            blocks.append((ordered[:cursor], validation))
        cursor = stop
    return blocks


def predictions_for(
    model: LogisticModel,
    examples: Sequence[Example],
    calibration: Calibration = Calibration(),
) -> List[Dict[str, Any]]:
    rows = []
    for example in examples:
        raw = model.probability(example)
        probability = calibration.apply(raw)
        rows.append({
            "slateDateEt": example.day,
            "officialGamePk": example.game_id,
            "homeTeam": example.home_team,
            "awayTeam": example.away_team,
            "homeWon": example.outcome,
            "marketHomeProbability": example.market_probability,
            "rawHomeProbability": raw,
            "homeWinProbability": probability,
            "predictedHome": probability >= 0.5,
            "correct": int(probability >= 0.5) == example.outcome,
        })
    return rows


def market_predictions(examples: Sequence[Example]) -> List[Dict[str, Any]]:
    rows = []
    for example in examples:
        probability = _clip(example.market_probability, MIN_PROBABILITY, PROBABILITY_CAP)
        rows.append({
            "slateDateEt": example.day,
            "officialGamePk": example.game_id,
            "homeWon": example.outcome,
            "homeWinProbability": probability,
            "predictedHome": probability >= 0.5,
            "correct": int(probability >= 0.5) == example.outcome,
        })
    return rows


def evaluate(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_day: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(str(row.get("slateDateEt") or ""), []).append(row)
    daily = []
    for day in sorted(by_day):
        games = by_day[day]
        correct = sum(bool(row.get("correct")) for row in games)
        accuracy = correct / len(games) if games else 0.0
        daily.append({
            "slateDateEt": day,
            "gameCount": len(games),
            "correct": correct,
            "accuracy": round(accuracy, 8),
            "dayPassed": bool(games and accuracy + 1e-12 >= DAILY_TARGET),
        })
    count = len(rows)
    correct = sum(bool(row.get("correct")) for row in rows)
    brier = (
        sum(
            (_f(row.get("homeWinProbability"), 0.5) - int(row.get("homeWon") or 0)) ** 2
            for row in rows
        )
        / count
        if count
        else 1.0
    )
    log_loss = 0.0
    for row in rows:
        probability = _clip(_f(row.get("homeWinProbability"), 0.5), 1e-9, 1.0 - 1e-9)
        outcome = int(row.get("homeWon") or 0)
        log_loss += -(
            outcome * math.log(probability)
            + (1 - outcome) * math.log(1.0 - probability)
        )
    log_loss = log_loss / count if count else 1.0
    bins: Dict[int, List[Tuple[float, int]]] = {}
    for row in rows:
        probability = _f(row.get("homeWinProbability"), 0.5)
        bins.setdefault(min(9, int(probability * 10)), []).append(
            (probability, int(row.get("homeWon") or 0))
        )
    calibration_error = 0.0
    reliability = []
    for index in sorted(bins):
        values = bins[index]
        mean_probability = sum(probability for probability, _ in values) / len(values)
        outcome_rate = sum(outcome for _, outcome in values) / len(values)
        calibration_error += len(values) / max(1, count) * abs(mean_probability - outcome_rate)
        reliability.append({
            "bin": index,
            "count": len(values),
            "meanProbability": round(mean_probability, 8),
            "outcomeRate": round(outcome_rate, 8),
        })
    accuracies = [row["accuracy"] for row in daily]
    return {
        "gameCount": count,
        "dayCount": len(daily),
        "correct": correct,
        "overallAccuracy": round(correct / count, 8) if count else 0.0,
        "meanDailyAccuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
        "minimumDailyAccuracy": min(accuracies) if accuracies else 0.0,
        "dailyPassRate": sum(row["dayPassed"] for row in daily) / len(daily) if daily else 0.0,
        "brierScore": round(brier, 8),
        "logLoss": round(log_loss, 8),
        "expectedCalibrationError": round(calibration_error, 8),
        "maximumProbability": round(
            max((_f(row.get("homeWinProbability"), 0.5) for row in rows), default=0.5), 8
        ),
        "minimumProbability": round(
            min((_f(row.get("homeWinProbability"), 0.5) for row in rows), default=0.5), 8
        ),
        "daily": daily,
        "reliability": reliability,
    }


def _rank(metrics: Mapping[str, Any], feature_count: int) -> Tuple[float, ...]:
    return (
        float(metrics.get("meanDailyAccuracy") or 0.0),
        float(metrics.get("overallAccuracy") or 0.0),
        -float(metrics.get("brierScore") or 1.0),
        -float(metrics.get("logLoss") or 10.0),
        -float(metrics.get("expectedCalibrationError") or 1.0),
        -float(feature_count),
    )
