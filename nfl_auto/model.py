"""Deterministic residual logistic models with season-based out-of-time audit."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .canonical import digest
from .config import parse_utc
from .features import FEATURE_NAMES, FrozenFeatureRow

MODEL_FAMILY = "nfl-auto-market-residual-logistic-v1"


def _clip(value: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, float(value)))


def _logit(probability: float) -> float:
    value = _clip(probability)
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp_value = math.exp(-min(value, 700.0))
        return 1.0 / (1.0 + exp_value)
    exp_value = math.exp(max(value, -700.0))
    return exp_value / (1.0 + exp_value)


@dataclass(frozen=True)
class TrainingRow:
    target: str
    event_key: str
    season: int
    week: int
    kickoff_utc: str
    features: tuple[float, ...]
    market_prior: float
    label: int
    feature_hash: str
    bbd_digest: str
    odds_digest: str

    @classmethod
    def from_frozen(cls, row: FrozenFeatureRow) -> "TrainingRow":
        return cls(
            target=row.target,
            event_key=row.event_key,
            season=row.season,
            week=row.week,
            kickoff_utc=row.kickoff_utc,
            features=row.features,
            market_prior=row.market_prior,
            label=row.label,
            feature_hash=row.feature_hash,
            bbd_digest=row.bbd_digest,
            odds_digest=row.odds_digest,
        )

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TrainingRow":
        feature_names = tuple(row.get("feature_names") or FEATURE_NAMES)
        if feature_names != FEATURE_NAMES:
            raise ValueError("NFL_FEATURE_SCHEMA_MISMATCH")
        return cls(
            target=str(row["target"]),
            event_key=str(row["event_key"]),
            season=int(row["season"]),
            week=int(row["week"]),
            kickoff_utc=str(row["kickoff_utc"]),
            features=tuple(float(value) for value in row["features"]),
            market_prior=float(row["market_prior"]),
            label=int(row["label"]),
            feature_hash=str(row["feature_hash"]),
            bbd_digest=str(row["bbd_digest"]),
            odds_digest=str(row["odds_digest"]),
        )

    def __post_init__(self) -> None:
        if self.label not in (0, 1):
            raise ValueError("NFL_LABEL_MUST_BE_BINARY")
        if not 0.0 < self.market_prior < 1.0:
            raise ValueError("NFL_MARKET_PRIOR_INVALID")
        if len(self.features) != len(FEATURE_NAMES):
            raise ValueError("NFL_FEATURE_VECTOR_LENGTH_INVALID")
        if not self.bbd_digest or not self.odds_digest:
            raise ValueError("NFL_DUAL_PROVIDER_PROVENANCE_REQUIRED")
        parse_utc(self.kickoff_utc)


@dataclass(frozen=True)
class SplitRows:
    train: tuple[TrainingRow, ...]
    validation: tuple[TrainingRow, ...]
    audit: tuple[TrainingRow, ...]


def season_split(
    rows: Iterable[TrainingRow],
    *,
    train_seasons: Sequence[int] = (2020, 2021, 2022, 2023),
    validation_seasons: Sequence[int] = (2024,),
    audit_seasons: Sequence[int] = (2025,),
) -> SplitRows:
    deduped: dict[tuple[str, str], TrainingRow] = {}
    for row in rows:
        key = (row.target, row.event_key)
        existing = deduped.get(key)
        if existing and existing.feature_hash != row.feature_hash:
            raise ValueError(f"NFL_MULTIPLE_FROZEN_VECTORS:{row.target}:{row.event_key}")
        deduped[key] = row
    ordered = sorted(
        deduped.values(),
        key=lambda row: (parse_utc(row.kickoff_utc), row.event_key),
    )
    train_set = set(int(value) for value in train_seasons)
    validation_set = set(int(value) for value in validation_seasons)
    audit_set = set(int(value) for value in audit_seasons)
    if train_set & validation_set or train_set & audit_set or validation_set & audit_set:
        raise ValueError("NFL_SEASON_SPLIT_OVERLAP")
    split = SplitRows(
        train=tuple(row for row in ordered if row.season in train_set),
        validation=tuple(row for row in ordered if row.season in validation_set),
        audit=tuple(row for row in ordered if row.season in audit_set),
    )
    if split.train and split.validation:
        if max(parse_utc(row.kickoff_utc) for row in split.train) >= min(
            parse_utc(row.kickoff_utc) for row in split.validation
        ):
            raise ValueError("NFL_TRAIN_VALIDATION_CHRONOLOGY_VIOLATION")
    if split.validation and split.audit:
        if max(parse_utc(row.kickoff_utc) for row in split.validation) >= min(
            parse_utc(row.kickoff_utc) for row in split.audit
        ):
            raise ValueError("NFL_VALIDATION_AUDIT_CHRONOLOGY_VIOLATION")
    return split


def adaptive_split(
    rows: Iterable[TrainingRow],
    *,
    live_season: int = 2026,
    min_live_rows: int = 144,
    live_validation_rows: int = 48,
    live_audit_rows: int = 48,
) -> tuple[SplitRows, str]:
    """Use the pristine 2025 audit until enough prospective 2026 evidence exists.

    Once the live corpus is large enough, every promoted challenger is evaluated
    on the most recent, never-trained-on live block. This lets the system learn
    from settled 2026 games without turning the original historical audit into
    reusable training leakage.
    """
    all_rows = list(rows)
    live = sorted(
        [row for row in all_rows if row.season == live_season],
        key=lambda row: (parse_utc(row.kickoff_utc), row.event_key),
    )
    reserved = int(live_validation_rows) + int(live_audit_rows)
    if len(live) < max(int(min_live_rows), reserved + 1):
        return season_split(all_rows), "HISTORICAL_2025_AUDIT"
    validation_start = len(live) - reserved
    audit_start = len(live) - int(live_audit_rows)
    train = [row for row in all_rows if row.season < live_season]
    train.extend(live[:validation_start])
    validation = live[validation_start:audit_start]
    audit = live[audit_start:]
    split = SplitRows(
        train=tuple(sorted(train, key=lambda row: (parse_utc(row.kickoff_utc), row.event_key))),
        validation=tuple(validation),
        audit=tuple(audit),
    )
    if max(parse_utc(row.kickoff_utc) for row in split.train) >= min(
        parse_utc(row.kickoff_utc) for row in split.validation
    ):
        raise ValueError("NFL_LIVE_TRAIN_VALIDATION_CHRONOLOGY_VIOLATION")
    if max(parse_utc(row.kickoff_utc) for row in split.validation) >= min(
        parse_utc(row.kickoff_utc) for row in split.audit
    ):
        raise ValueError("NFL_LIVE_VALIDATION_AUDIT_CHRONOLOGY_VIOLATION")
    return split, "LIVE_EXPANDING_PROSPECTIVE_AUDIT"


@dataclass
class ResidualLogisticModel:
    target: str
    feature_names: tuple[str, ...]
    means: list[float]
    scales: list[float]
    weights: list[float]
    temperature: float = 1.0

    @classmethod
    def initialize(cls, target: str, feature_names: Sequence[str] = FEATURE_NAMES) -> "ResidualLogisticModel":
        size = len(feature_names)
        return cls(
            target=target,
            feature_names=tuple(feature_names),
            means=[0.0] * size,
            scales=[1.0] * size,
            weights=[0.0] * (size + 1),
            temperature=1.0,
        )

    def standardize(self, features: Sequence[float]) -> list[float]:
        if len(features) != len(self.feature_names):
            raise ValueError("NFL_MODEL_FEATURE_VECTOR_LENGTH_INVALID")
        return [
            (float(value) - self.means[index]) / self.scales[index]
            for index, value in enumerate(features)
        ]

    def raw_logit(self, features: Sequence[float], market_prior: float) -> float:
        x = [1.0] + self.standardize(features)
        return _logit(market_prior) + sum(weight * value for weight, value in zip(self.weights, x))

    def predict_probability(self, features: Sequence[float], market_prior: float) -> float:
        temperature = max(0.10, float(self.temperature))
        return _sigmoid(self.raw_logit(features, market_prior) / temperature)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "family": MODEL_FAMILY,
            "target": self.target,
            "feature_names": list(self.feature_names),
            "means": list(self.means),
            "scales": list(self.scales),
            "weights": list(self.weights),
            "temperature": self.temperature,
        }
        payload["model_digest"] = digest(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResidualLogisticModel":
        if payload.get("family") != MODEL_FAMILY:
            raise ValueError("NFL_MODEL_FAMILY_UNSUPPORTED")
        model = cls(
            target=str(payload["target"]),
            feature_names=tuple(str(value) for value in payload["feature_names"]),
            means=[float(value) for value in payload["means"]],
            scales=[float(value) for value in payload["scales"]],
            weights=[float(value) for value in payload["weights"]],
            temperature=float(payload.get("temperature", 1.0)),
        )
        expected = payload.get("model_digest")
        if expected and expected != model.to_dict()["model_digest"]:
            raise ValueError("NFL_MODEL_DIGEST_MISMATCH")
        return model


def _fit_standardizer(model: ResidualLogisticModel, rows: Sequence[TrainingRow]) -> None:
    if not rows:
        raise ValueError("NFL_EMPTY_TRAINING_SET")
    for index in range(len(model.feature_names)):
        values = [row.features[index] for row in rows]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        model.means[index] = mean
        model.scales[index] = math.sqrt(variance) if variance > 1e-10 else 1.0


def fit_model(
    rows: Sequence[TrainingRow],
    target: str,
    *,
    learning_rate: float = 0.025,
    l2: float = 0.001,
    epochs: int = 80,
) -> ResidualLogisticModel:
    if not rows or any(row.target != target for row in rows):
        raise ValueError("NFL_TARGET_TRAINING_ROWS_INVALID")
    model = ResidualLogisticModel.initialize(target)
    _fit_standardizer(model, rows)
    ordered = sorted(rows, key=lambda row: (parse_utc(row.kickoff_utc), row.event_key))
    for epoch in range(max(1, int(epochs))):
        gradient = [0.0] * len(model.weights)
        for row in ordered:
            x = [1.0] + model.standardize(row.features)
            probability = _sigmoid(
                _logit(row.market_prior)
                + sum(weight * value for weight, value in zip(model.weights, x))
            )
            error = probability - row.label
            for index, value in enumerate(x):
                gradient[index] += error * value
        step = float(learning_rate) / math.sqrt(1.0 + epoch * 0.03)
        count = float(len(ordered))
        for index in range(len(model.weights)):
            regularization = 0.0 if index == 0 else float(l2) * model.weights[index]
            model.weights[index] -= step * (gradient[index] / count + regularization)
    return model


def binary_metrics(probabilities: Sequence[float], labels: Sequence[int]) -> dict[str, float | int]:
    if len(probabilities) != len(labels):
        raise ValueError("NFL_METRIC_LENGTH_MISMATCH")
    if not labels:
        return {
            "count": 0,
            "log_loss": 0.0,
            "brier": 0.0,
            "accuracy": 0.0,
            "ece": 0.0,
        }
    loss = 0.0
    brier = 0.0
    correct = 0
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probability, label in zip(probabilities, labels):
        p = _clip(probability)
        loss -= label * math.log(p) + (1 - label) * math.log(1.0 - p)
        brier += (p - label) ** 2
        predicted = int(p >= 0.5)
        correct += int(predicted == label)
        confidence = p if predicted else 1.0 - p
        bins[min(9, int(confidence * 10))].append((confidence, int(predicted == label)))
    count = len(labels)
    ece = 0.0
    for bucket in bins:
        if bucket:
            confidence = sum(row[0] for row in bucket) / len(bucket)
            accuracy = sum(row[1] for row in bucket) / len(bucket)
            ece += len(bucket) / count * abs(confidence - accuracy)
    return {
        "count": count,
        "log_loss": loss / count,
        "brier": brier / count,
        "accuracy": correct / count,
        "ece": ece,
    }


def evaluate(model: ResidualLogisticModel, rows: Sequence[TrainingRow]) -> dict[str, Any]:
    candidate = [model.predict_probability(row.features, row.market_prior) for row in rows]
    baseline = [row.market_prior for row in rows]
    labels = [row.label for row in rows]
    candidate_metrics = binary_metrics(candidate, labels)
    baseline_metrics = binary_metrics(baseline, labels)
    return {
        "candidate": candidate_metrics,
        "market_baseline": baseline_metrics,
        "log_loss_skill": float(baseline_metrics["log_loss"]) - float(candidate_metrics["log_loss"]),
    }


def fit_temperature(model: ResidualLogisticModel, rows: Sequence[TrainingRow]) -> float:
    if not rows:
        model.temperature = 1.0
        return model.temperature
    logits = [model.raw_logit(row.features, row.market_prior) for row in rows]
    labels = [row.label for row in rows]
    best = (float("inf"), 1.0)
    for step in range(20, 401):
        temperature = step / 100.0
        metrics = binary_metrics([_sigmoid(value / temperature) for value in logits], labels)
        candidate = (float(metrics["log_loss"]), temperature)
        if candidate < best:
            best = candidate
    model.temperature = best[1]
    return model.temperature


def paired_skill_lower_bound(
    candidate_probabilities: Sequence[float],
    baseline_probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    samples: int = 2000,
    seed: int = 7609,
    quantile: float = 0.05,
) -> float:
    if not labels or len(candidate_probabilities) != len(labels) or len(baseline_probabilities) != len(labels):
        return float("-inf")
    deltas: list[float] = []
    for candidate, baseline, label in zip(candidate_probabilities, baseline_probabilities, labels):
        candidate_label = _clip(candidate if label else 1.0 - candidate)
        baseline_label = _clip(baseline if label else 1.0 - baseline)
        deltas.append(math.log(candidate_label / baseline_label))
    rng = random.Random(seed)
    values = [sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(samples)]
    values.sort()
    return values[min(len(values) - 1, max(0, int(len(values) * quantile)))]


def model_skill_lower_bound(model: ResidualLogisticModel, rows: Sequence[TrainingRow]) -> float:
    return paired_skill_lower_bound(
        [model.predict_probability(row.features, row.market_prior) for row in rows],
        [row.market_prior for row in rows],
        [row.label for row in rows],
    )


def select_candidate(
    split: SplitRows,
    target: str,
    *,
    search: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[ResidualLogisticModel, dict[str, Any]]:
    if not split.train or not split.validation or not split.audit:
        raise ValueError("NFL_CHRONOLOGICAL_TRAIN_VALIDATION_AUDIT_REQUIRED")
    trials = search or (
        {"learning_rate": 0.010, "l2": 0.0005, "epochs": 60},
        {"learning_rate": 0.025, "l2": 0.0010, "epochs": 80},
        {"learning_rate": 0.040, "l2": 0.0030, "epochs": 110},
        {"learning_rate": 0.020, "l2": 0.0100, "epochs": 130},
    )
    ranked: list[tuple[float, str, ResidualLogisticModel, dict[str, Any], dict[str, Any]]] = []
    for raw_params in trials:
        params = {
            "learning_rate": float(raw_params["learning_rate"]),
            "l2": float(raw_params["l2"]),
            "epochs": int(raw_params["epochs"]),
        }
        model = fit_model(split.train, target, **params)
        fit_temperature(model, split.validation)
        validation = evaluate(model, split.validation)
        ranked.append(
            (
                float(validation["candidate"]["log_loss"]),
                digest(params),
                model,
                params,
                validation,
            )
        )
    _, _, best_model, best_params, validation = min(ranked, key=lambda row: (row[0], row[1]))
    audit = evaluate(best_model, split.audit)
    report = {
        "family": MODEL_FAMILY,
        "target": target,
        "parameters": best_params,
        "temperature": best_model.temperature,
        "split_counts": {
            "train": len(split.train),
            "validation": len(split.validation),
            "audit": len(split.audit),
        },
        "seasons": {
            "train": sorted({row.season for row in split.train}),
            "validation": sorted({row.season for row in split.validation}),
            "audit": sorted({row.season for row in split.audit}),
        },
        "validation": validation,
        "audit": audit,
        "audit_market_skill_lower_bound_95": model_skill_lower_bound(best_model, split.audit),
        "training_manifest_digest": digest(
            [(row.event_key, row.feature_hash) for row in split.train]
        ),
        "validation_manifest_digest": digest(
            [(row.event_key, row.feature_hash) for row in split.validation]
        ),
        "audit_manifest_digest": digest(
            [(row.event_key, row.feature_hash) for row in split.audit]
        ),
    }
    return best_model, report
