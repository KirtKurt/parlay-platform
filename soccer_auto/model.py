"""Deterministic three-class residual model and chronological evaluation.

The model learns corrections to the de-vigged same-time bookmaker consensus.  It
never treats closing odds as labels and never random-splits events across folds.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .canonical import digest, parse_utc


CLASSES = ("home", "draw", "away")
MODEL_FAMILY = "soccer-auto-residual-softmax-v1"


def _clip_probability(value: float) -> float:
    return min(1.0 - 1e-12, max(1e-12, float(value)))


def _softmax(logits: Sequence[float]) -> list[float]:
    peak = max(logits)
    values = [math.exp(value - peak) for value in logits]
    total = sum(values)
    return [value / total for value in values]


@dataclass(frozen=True)
class TrainingRow:
    event_key: str
    commence_time: str
    feature_hash: str
    features: tuple[float, ...]
    market_prior: tuple[float, float, float]
    label: int
    competition: str

    def __post_init__(self) -> None:
        if self.label not in (0, 1, 2):
            raise ValueError("label must be home=0, draw=1, or away=2")
        if len(self.market_prior) != 3 or any(value <= 0 for value in self.market_prior):
            raise ValueError("market prior must contain three positive probabilities")
        if abs(sum(self.market_prior) - 1.0) > 1e-6:
            raise ValueError("market prior must sum to one")

    @property
    def timestamp(self) -> datetime:
        return parse_utc(self.commence_time)


@dataclass(frozen=True)
class SplitRows:
    train: tuple[TrainingRow, ...]
    validation: tuple[TrainingRow, ...]
    audit: tuple[TrainingRow, ...]


def chronological_split(
    rows: Iterable[TrainingRow],
    *,
    train_fraction: float = 0.65,
    validation_fraction: float = 0.15,
    embargo_seconds: int = 3600,
) -> SplitRows:
    """Group by event, sort by kickoff, and embargo split boundaries."""
    deduped: dict[str, TrainingRow] = {}
    for row in rows:
        existing = deduped.get(row.event_key)
        if existing and existing.feature_hash != row.feature_hash:
            raise ValueError(f"multiple frozen vectors for event {row.event_key}")
        deduped[row.event_key] = row
    ordered = sorted(deduped.values(), key=lambda row: (row.timestamp, row.event_key))
    if len(ordered) < 3:
        return SplitRows(tuple(ordered), (), ())
    train_end = max(1, int(len(ordered) * train_fraction))
    validation_end = max(train_end + 1, int(len(ordered) * (train_fraction + validation_fraction)))
    validation_end = min(validation_end, len(ordered) - 1)
    train_cutoff = ordered[train_end].timestamp
    audit_cutoff = ordered[validation_end].timestamp
    train = tuple(row for row in ordered[:train_end] if (train_cutoff - row.timestamp).total_seconds() >= embargo_seconds)
    validation = tuple(
        row
        for row in ordered[train_end:validation_end]
        if (row.timestamp - train_cutoff).total_seconds() >= embargo_seconds
        and (audit_cutoff - row.timestamp).total_seconds() >= embargo_seconds
    )
    audit = tuple(
        row
        for row in ordered[validation_end:]
        if (row.timestamp - audit_cutoff).total_seconds() >= embargo_seconds
    )
    return SplitRows(train, validation, audit)


@dataclass
class ResidualSoftmaxModel:
    feature_names: tuple[str, ...]
    means: list[float]
    scales: list[float]
    weights: list[list[float]]
    temperature: float = 1.0

    @classmethod
    def initialize(cls, feature_names: Sequence[str]) -> "ResidualSoftmaxModel":
        size = len(feature_names)
        return cls(
            feature_names=tuple(feature_names),
            means=[0.0] * size,
            scales=[1.0] * size,
            weights=[[0.0] * (size + 1) for _ in CLASSES],
        )

    def _standardize(self, features: Sequence[float]) -> list[float]:
        if len(features) != len(self.feature_names):
            raise ValueError("feature vector length does not match model schema")
        return [
            (float(value) - self.means[index]) / self.scales[index]
            for index, value in enumerate(features)
        ]

    def raw_logits(self, features: Sequence[float], market_prior: Sequence[float]) -> list[float]:
        x = [1.0] + self._standardize(features)
        return [
            math.log(_clip_probability(market_prior[class_index]))
            + sum(weight * value for weight, value in zip(self.weights[class_index], x))
            for class_index in range(3)
        ]

    def predict_proba(self, features: Sequence[float], market_prior: Sequence[float]) -> list[float]:
        temperature = max(0.05, float(self.temperature))
        return _softmax([value / temperature for value in self.raw_logits(features, market_prior)])

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "family": MODEL_FAMILY,
            "feature_names": list(self.feature_names),
            "means": self.means,
            "scales": self.scales,
            "weights": self.weights,
            "temperature": self.temperature,
        }
        payload["model_digest"] = digest(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResidualSoftmaxModel":
        if payload.get("family") != MODEL_FAMILY:
            raise ValueError("unsupported soccer model family")
        model = cls(
            feature_names=tuple(payload["feature_names"]),
            means=[float(value) for value in payload["means"]],
            scales=[float(value) for value in payload["scales"]],
            weights=[[float(value) for value in row] for row in payload["weights"]],
            temperature=float(payload.get("temperature", 1.0)),
        )
        expected = payload.get("model_digest")
        if expected and model.to_dict()["model_digest"] != expected:
            raise ValueError("model artifact digest mismatch")
        return model


def _fit_standardizer(model: ResidualSoftmaxModel, rows: Sequence[TrainingRow]) -> None:
    if not rows:
        raise ValueError("cannot fit an empty training set")
    for index in range(len(model.feature_names)):
        values = [row.features[index] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        model.means[index] = mean
        model.scales[index] = math.sqrt(variance) if variance > 1e-12 else 1.0


def fit_model(
    rows: Sequence[TrainingRow],
    feature_names: Sequence[str],
    *,
    learning_rate: float = 0.03,
    l2: float = 0.001,
    epochs: int = 60,
) -> ResidualSoftmaxModel:
    model = ResidualSoftmaxModel.initialize(feature_names)
    _fit_standardizer(model, rows)
    ordered = sorted(rows, key=lambda row: (row.timestamp, row.event_key))
    for epoch in range(max(1, epochs)):
        gradient = [[0.0] * len(model.weights[0]) for _ in CLASSES]
        for row in ordered:
            x = [1.0] + model._standardize(row.features)
            probabilities = _softmax(
                [
                    math.log(_clip_probability(row.market_prior[class_index]))
                    + sum(weight * value for weight, value in zip(model.weights[class_index], x))
                    for class_index in range(3)
                ]
            )
            for class_index in range(3):
                error = probabilities[class_index] - float(row.label == class_index)
                for feature_index, value in enumerate(x):
                    gradient[class_index][feature_index] += error * value
        step = learning_rate / math.sqrt(1.0 + epoch * 0.02)
        count = float(len(ordered))
        for class_index in range(3):
            for feature_index in range(len(model.weights[class_index])):
                regularization = 0.0 if feature_index == 0 else l2 * model.weights[class_index][feature_index]
                model.weights[class_index][feature_index] -= step * (
                    gradient[class_index][feature_index] / count + regularization
                )
    return model


def multiclass_metrics(probabilities: Sequence[Sequence[float]], labels: Sequence[int]) -> dict[str, float | int]:
    if len(probabilities) != len(labels):
        raise ValueError("probabilities and labels have different lengths")
    if not labels:
        return {"count": 0, "log_loss": 0.0, "brier": 0.0, "rps": 0.0, "accuracy": 0.0, "ece": 0.0}
    log_loss = 0.0
    brier = 0.0
    rps = 0.0
    correct = 0
    confidence_bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probs, label in zip(probabilities, labels):
        if len(probs) != 3:
            raise ValueError("three probabilities are required")
        normalized_total = sum(probs)
        row = [_clip_probability(value / normalized_total) for value in probs]
        log_loss -= math.log(row[label])
        brier += sum((row[index] - float(index == label)) ** 2 for index in range(3)) / 3.0
        observed = [float(label <= boundary) for boundary in range(2)]
        predicted = [sum(row[: boundary + 1]) for boundary in range(2)]
        rps += sum((p - o) ** 2 for p, o in zip(predicted, observed)) / 2.0
        predicted_class = max(range(3), key=lambda index: row[index])
        correct += int(predicted_class == label)
        confidence = row[predicted_class]
        confidence_bins[min(9, int(confidence * 10))].append((confidence, int(predicted_class == label)))
    count = len(labels)
    ece = 0.0
    for bucket in confidence_bins:
        if bucket:
            average_confidence = sum(row[0] for row in bucket) / len(bucket)
            average_accuracy = sum(row[1] for row in bucket) / len(bucket)
            ece += len(bucket) / count * abs(average_confidence - average_accuracy)
    return {
        "count": count,
        "log_loss": log_loss / count,
        "brier": brier / count,
        "rps": rps / count,
        "accuracy": correct / count,
        "ece": ece,
    }


def evaluate(model: ResidualSoftmaxModel, rows: Sequence[TrainingRow]) -> dict[str, Any]:
    candidate = [model.predict_proba(row.features, row.market_prior) for row in rows]
    baseline = [list(row.market_prior) for row in rows]
    labels = [row.label for row in rows]
    return {
        "candidate": multiclass_metrics(candidate, labels),
        "market_baseline": multiclass_metrics(baseline, labels),
        "log_loss_skill": (
            multiclass_metrics(baseline, labels)["log_loss"]
            - multiclass_metrics(candidate, labels)["log_loss"]
        ),
    }


def fit_temperature(model: ResidualSoftmaxModel, rows: Sequence[TrainingRow]) -> float:
    if not rows:
        model.temperature = 1.0
        return 1.0
    logits = [model.raw_logits(row.features, row.market_prior) for row in rows]
    best = (float("inf"), 1.0)
    for step in range(10, 401):
        temperature = step / 100.0
        metrics = multiclass_metrics(
            [_softmax([value / temperature for value in row]) for row in logits],
            [row.label for row in rows],
        )
        candidate = (float(metrics["log_loss"]), temperature)
        if candidate < best:
            best = candidate
    model.temperature = best[1]
    return best[1]


def paired_log_loss_skill_lower_bound(
    model: ResidualSoftmaxModel,
    rows: Sequence[TrainingRow],
    *,
    samples: int = 1000,
    seed: int = 7331,
    quantile: float = 0.05,
) -> float:
    if not rows:
        return float("-inf")
    deltas = []
    for row in rows:
        candidate = model.predict_proba(row.features, row.market_prior)
        deltas.append(
            -math.log(_clip_probability(row.market_prior[row.label]))
            + math.log(_clip_probability(candidate[row.label]))
        )
    rng = random.Random(seed)
    bootstrapped = []
    for _ in range(samples):
        bootstrapped.append(sum(rng.choice(deltas) for _ in deltas) / len(deltas))
    bootstrapped.sort()
    return bootstrapped[min(len(bootstrapped) - 1, max(0, int(len(bootstrapped) * quantile)))]


def paired_skill_lower_bound_from_probabilities(
    candidate: Sequence[Sequence[float]],
    comparator: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    samples: int = 1000,
    seed: int = 7331,
    quantile: float = 0.05,
) -> float:
    if not labels or len(candidate) != len(labels) or len(comparator) != len(labels):
        return float("-inf")
    deltas = [
        -math.log(_clip_probability(comparator[index][label]))
        + math.log(_clip_probability(candidate[index][label]))
        for index, label in enumerate(labels)
    ]
    rng = random.Random(seed)
    values = [sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(samples)]
    values.sort()
    return values[min(len(values) - 1, max(0, int(len(values) * quantile)))]


def select_candidate(
    split: SplitRows,
    feature_names: Sequence[str],
    *,
    search: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[ResidualSoftmaxModel, dict[str, Any]]:
    if not split.train or not split.validation:
        raise ValueError("chronological train and validation evidence are required")
    candidates = search or (
        {"learning_rate": 0.01, "l2": 0.0005, "epochs": 40},
        {"learning_rate": 0.03, "l2": 0.001, "epochs": 60},
        {"learning_rate": 0.05, "l2": 0.005, "epochs": 80},
    )
    ranked = []
    for params in candidates:
        model = fit_model(split.train, feature_names, **params)
        fit_temperature(model, split.validation)
        validation = evaluate(model, split.validation)
        ranked.append((float(validation["candidate"]["log_loss"]), digest(params), model, dict(params), validation))
    _, _, best, params, validation = min(ranked, key=lambda row: (row[0], row[1]))
    audit = evaluate(best, split.audit)
    lower_bound = paired_log_loss_skill_lower_bound(best, split.audit) if split.audit else float("-inf")
    report = {
        "family": MODEL_FAMILY,
        "parameters": params,
        "split_counts": {
            "train": len(split.train),
            "validation": len(split.validation),
            "audit": len(split.audit),
        },
        "validation": validation,
        "audit": audit,
        "audit_log_loss_skill_lower_bound_95": lower_bound,
        "data_manifest_digest": digest(
            [(row.event_key, row.feature_hash, row.label) for row in split.train + split.validation + split.audit]
        ),
    }
    return best, report
