"""LightGBM custom Brier objective for KS1 challenger runs.

Serving train.py still defaults to objective=binary (log loss).
This module must not be used to overwrite model_refs.
"""
import numpy as np

EPS = 1e-12


def _probability(raw):
    raw = np.clip(np.asarray(raw, dtype=float), -20.0, 20.0)
    return 1.0 / (1.0 + np.exp(-raw))


def brier_objective(preds, dataset):
    """grad/hess of (p - y)^2 with p = sigmoid(raw score)."""
    y = np.asarray(dataset.get_label(), dtype=float)
    p = _probability(preds)
    scale = p * (1.0 - p)
    grad = 2.0 * (p - y) * scale
    hess = 2.0 * scale * ((1.0 - 2.0 * p) * (p - y) + scale)
    return grad, np.maximum(hess, EPS)


def brier_metric(preds, dataset):
    y = np.asarray(dataset.get_label(), dtype=float)
    p = _probability(preds)
    return "brier", float(np.mean((p - y) ** 2)), False


def booster_params(base):
    """Drop the built-in binary objective so fobj owns the loss."""
    params = dict(base)
    params.pop("objective", None)
    params.pop("n_estimators", None)
    params.setdefault("verbosity", -1)
    params.setdefault("deterministic", True)
    return params
