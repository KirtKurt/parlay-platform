from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Dict, Mapping

from .features import FEATURE_NAMES, build, vector

LEARNING_RATE = Decimal("0.03")
L2 = Decimal("0.0005")


def sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def initial_state() -> Dict[str, Any]:
    return {
        "weights": [Decimal("2.0")] + [Decimal("0")] * (len(FEATURE_NAMES) - 1),
        "bias": Decimal("-1.0"),
        "version": 1,
        "training_samples": 0,
    }


def predict_probability(state: Mapping[str, Any], signals: Mapping[str, Any]) -> tuple[float, Dict[str, float]]:
    feat = build(signals)
    z = float(state["bias"]) + sum(float(w) * x for w, x in zip(state["weights"], vector(feat)))
    return sigmoid(z), feat


def sgd_step(state: Mapping[str, Any], signals: Mapping[str, Any], player_won: bool) -> Dict[str, Any]:
    prob, feat = predict_probability(state, signals)
    error = Decimal(str((1 if player_won else 0) - prob))
    xs = [Decimal(str(v)) for v in vector(feat)]
    weights = [Decimal(str(w)) for w in state["weights"]]
    new_weights = [w + LEARNING_RATE * (error * x - L2 * w) for w, x in zip(weights, xs)]
    return {
        "weights": new_weights,
        "bias": Decimal(str(state["bias"])) + LEARNING_RATE * error,
        "version": int(state["version"]) + 1,
        "training_samples": int(state["training_samples"]) + 1,
        "pre_update_probability": prob,
        "features": feat,
    }
