from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

DEFAULT_FEATURES = (
    'market_home_probability', 'market_home_logit', 'book_divergence',
    'book_count_log1p', 'pull_count_log1p', 'market_move', 'market_velocity',
    'market_volatility', 'market_reversals', 'market_key_count',
    'bookmaker_count_all_markets', 'player_prop_market_count',
    'period_market_count', 'market_outcome_count', 'has_alternate_lines',
    'has_team_totals', 'hours_to_first_pitch', 'home_spread_consensus',
    'home_spread_cover_probability', 'game_total_consensus', 'over_probability',
)


def _feature_value(row: Mapping[str, float], name: str) -> float:
    """Materialize autonomous interaction features identically in training and inference."""
    if name.endswith('__sq'):
        base = name[:-4]
        v = float(row.get(base, 0.0))
        return v * v
    if '__x__' in name:
        left, right = name.split('__x__', 1)
        return float(row.get(left, 0.0)) * float(row.get(right, 0.0))
    return float(row.get(name, 0.0))


@dataclass(frozen=True)
class Model:
    intercept: float
    weights: tuple[float, ...]
    feature_names: tuple[str, ...] = DEFAULT_FEATURES
    model_family: str = 'logistic'
    metadata: dict | None = None

    def predict(self, row: Mapping[str, float]) -> float:
        z = self.intercept + sum(w * _feature_value(row, n) for w, n in zip(self.weights, self.feature_names))
        z = max(-30., min(30., z))
        return 1 / (1 + math.exp(-z))

    def dumps(self) -> str:
        return json.dumps({
            'intercept': self.intercept,
            'weights': self.weights,
            'feature_names': self.feature_names,
            'model_family': self.model_family,
            'metadata': self.metadata or {},
        }, sort_keys=True)

    @classmethod
    def loads(cls, payload: str | Mapping) -> 'Model':
        obj = json.loads(payload) if isinstance(payload, str) else dict(payload)
        return cls(
            float(obj['intercept']), tuple(float(x) for x in obj['weights']),
            tuple(obj.get('feature_names') or DEFAULT_FEATURES),
            str(obj.get('model_family') or 'logistic'), dict(obj.get('metadata') or {}),
        )


def train_logistic(rows: Sequence[Mapping[str, float]], labels: Sequence[int], *,
                   feature_names: Sequence[str] | None = None, epochs: int = 500,
                   lr: float = .03, l2: float = .001, metadata: dict | None = None) -> Model:
    names = tuple(feature_names or DEFAULT_FEATURES)
    if len(rows) != len(labels) or len(rows) < 10 or not names:
        raise ValueError('INSUFFICIENT_OR_MISMATCHED_TRAINING_ROWS')
    w = [0.0] * len(names)
    b = 0.0
    for _ in range(int(epochs)):
        gb = 0.0
        gw = [0.0] * len(w)
        for row, y in zip(rows, labels):
            x = [_feature_value(row, n) for n in names]
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
    return Model(b, tuple(w), names, 'logistic', metadata)


def log_loss(model: Model, rows, labels) -> float:
    eps = 1e-9
    vals = []
    for row, y in zip(rows, labels):
        p = max(eps, min(1 - eps, model.predict(row)))
        vals.append(-(int(y) * math.log(p) + (1 - int(y)) * math.log(1 - p)))
    return sum(vals) / len(vals)


def brier_score(model: Model, rows, labels) -> float:
    return sum((model.predict(r) - int(y)) ** 2 for r, y in zip(rows, labels)) / len(rows)


def calibration_error(model: Model, rows, labels, bins: int = 10) -> float:
    groups = [[] for _ in range(bins)]
    for row, y in zip(rows, labels):
        p = model.predict(row)
        groups[min(bins - 1, int(p * bins))].append((p, int(y)))
    total = max(1, len(rows))
    return sum((len(g)/total) * abs(sum(p for p,_ in g)/len(g) - sum(y for _,y in g)/len(g))
               for g in groups if g)


def chronological_split(rows, labels, holdout_fraction: float = .2):
    n = len(rows)
    cut = max(1, min(n - 1, int(n * (1 - holdout_fraction))))
    return rows[:cut], labels[:cut], rows[cut:], labels[cut:]


def promote_challenger(*, challenger: Model, incumbent: Model | None, validation_rows,
                       validation_labels, min_logloss_improvement: float = .001,
                       max_calibration_error: float = .10) -> dict:
    cl = log_loss(challenger, validation_rows, validation_labels)
    cb = brier_score(challenger, validation_rows, validation_labels)
    cc = calibration_error(challenger, validation_rows, validation_labels)
    if cc > max_calibration_error:
        return {'promote': False, 'reason': 'CALIBRATION_GATE', 'challengerLogLoss': cl,
                'challengerBrier': cb, 'challengerCalibrationError': cc}
    if incumbent is None:
        return {'promote': True, 'reason': 'FIRST_QUALIFIED_CHAMPION', 'challengerLogLoss': cl,
                'challengerBrier': cb, 'challengerCalibrationError': cc}
    il = log_loss(incumbent, validation_rows, validation_labels)
    improvement = il - cl
    return {'promote': improvement >= min_logloss_improvement,
            'reason': 'BEATS_INCUMBENT' if improvement >= min_logloss_improvement else 'NO_MATERIAL_IMPROVEMENT',
            'challengerLogLoss': cl, 'incumbentLogLoss': il, 'logLossImprovement': improvement,
            'challengerBrier': cb, 'challengerCalibrationError': cc}
