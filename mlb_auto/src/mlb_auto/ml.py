from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

FEATURES = (
    'market_home_probability',
    'market_home_logit',
    'book_divergence',
    'book_count_log1p',
    'pull_count_log1p',
    'market_move',
    'market_velocity',
    'market_volatility',
    'market_reversals',
    'market_key_count',
    'bookmaker_count_all_markets',
    'player_prop_market_count',
    'period_market_count',
    'market_outcome_count',
    'has_alternate_lines',
    'has_team_totals',
    'hours_to_first_pitch',
)


@dataclass(frozen=True)
class Model:
    intercept: float
    weights: tuple[float, ...]
    feature_names: tuple[str, ...] = FEATURES

    def predict(self, row: Mapping[str, float]) -> float:
        z = self.intercept + sum(w * float(row.get(n, 0.0)) for w, n in zip(self.weights, self.feature_names))
        z = max(-30., min(30., z))
        return 1 / (1 + math.exp(-z))

    def dumps(self) -> str:
        return json.dumps(
            {'intercept': self.intercept, 'weights': self.weights, 'feature_names': self.feature_names},
            sort_keys=True,
        )


def train_logistic(
    rows: Sequence[Mapping[str, float]],
    labels: Sequence[int],
    *,
    epochs: int = 500,
    lr: float = .03,
    l2: float = .001,
) -> Model:
    if len(rows) != len(labels) or len(rows) < 10:
        raise ValueError('INSUFFICIENT_OR_MISMATCHED_TRAINING_ROWS')
    w = [0.0] * len(FEATURES)
    b = 0.0
    for _ in range(epochs):
        gb = 0.0
        gw = [0.0] * len(w)
        for row, y in zip(rows, labels):
            x = [float(row.get(n, 0.0)) for n in FEATURES]
            z = max(-30., min(30., b + sum(a * c for a, c in zip(w, x))))
            p = 1 / (1 + math.exp(-z))
            e = p - int(y)
            gb += e
            for i, v in enumerate(x):
                gw[i] += e * v
        n = float(len(rows))
        b -= lr * gb / n
        for i in range(len(w)):
            w[i] -= lr * (gw[i] / n + l2 * w[i])
    return Model(b, tuple(w))


def log_loss(model: Model, rows: Sequence[Mapping[str, float]], labels: Sequence[int]) -> float:
    eps = 1e-9
    vals = []
    for row, y in zip(rows, labels):
        p = max(eps, min(1 - eps, model.predict(row)))
        vals.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return sum(vals) / len(vals)


def brier_score(model: Model, rows: Sequence[Mapping[str, float]], labels: Sequence[int]) -> float:
    return sum((model.predict(row) - int(y)) ** 2 for row, y in zip(rows, labels)) / len(rows)


def calibration_error(model: Model, rows: Sequence[Mapping[str, float]], labels: Sequence[int], bins: int = 10) -> float:
    groups = [[] for _ in range(bins)]
    for row, y in zip(rows, labels):
        p = model.predict(row)
        groups[min(bins - 1, int(p * bins))].append((p, int(y)))
    total = max(1, len(rows))
    return sum(
        (len(group) / total) * abs(sum(p for p, _ in group) / len(group) - sum(y for _, y in group) / len(group))
        for group in groups if group
    )


def chronological_split(rows, labels, holdout_fraction: float = .2):
    n = len(rows)
    cut = max(1, min(n - 1, int(n * (1 - holdout_fraction))))
    return rows[:cut], labels[:cut], rows[cut:], labels[cut:]


def promote_challenger(*, challenger: Model, incumbent: Model | None, validation_rows, validation_labels,
                       min_logloss_improvement: float = .001, max_calibration_error: float = .10) -> dict:
    challenger_loss = log_loss(challenger, validation_rows, validation_labels)
    challenger_brier = brier_score(challenger, validation_rows, validation_labels)
    challenger_cal = calibration_error(challenger, validation_rows, validation_labels)
    if challenger_cal > max_calibration_error:
        return {'promote': False, 'reason': 'CALIBRATION_GATE', 'challengerLogLoss': challenger_loss,
                'challengerBrier': challenger_brier, 'challengerCalibrationError': challenger_cal}
    if incumbent is None:
        return {'promote': True, 'reason': 'FIRST_QUALIFIED_CHAMPION', 'challengerLogLoss': challenger_loss,
                'challengerBrier': challenger_brier, 'challengerCalibrationError': challenger_cal}
    incumbent_loss = log_loss(incumbent, validation_rows, validation_labels)
    improvement = incumbent_loss - challenger_loss
    return {
        'promote': improvement >= min_logloss_improvement,
        'reason': 'BEATS_INCUMBENT' if improvement >= min_logloss_improvement else 'NO_MATERIAL_IMPROVEMENT',
        'challengerLogLoss': challenger_loss,
        'incumbentLogLoss': incumbent_loss,
        'logLossImprovement': improvement,
        'challengerBrier': challenger_brier,
        'challengerCalibrationError': challenger_cal,
    }
