"""L2-regularized logistic on the *same information* LGB sees from the stack.

If this GLM cannot recover an LGB-sized edge, LGB found an interaction
that does not replicate. That is a diagnostic, not a production override.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from stack2mlb.bayes_market import expit
from stack2mlb.market import clip01


FEATURE_ORDER = (
    "p_poisson",
    "p_elo",
    "p_market",
    "lambda_diff",
    "lgb_minus_poisson",
)


@dataclass
class FittedGLM:
    coef: np.ndarray
    intercept: float
    features: tuple[str, ...]
    l2: float

    def predict(self, row: dict) -> float:
        x = np.array([float(row[name]) for name in self.features], dtype=float)
        return clip01(float(expit(self.intercept + float(x @ self.coef))))


def _design(rows: list[dict], features: tuple[str, ...]) -> np.ndarray:
    return np.array([[float(r[name]) for name in features] for r in rows], dtype=float)


def fit(rows: list[dict], labels: list[int], l2: float = 10.0, features: tuple[str, ...] = FEATURE_ORDER) -> FittedGLM:
    if len(rows) != len(labels):
        raise ValueError("row/label length mismatch")
    if len(rows) < 30:
        raise ValueError("GLM needs at least 30 graded rows")
    y = np.asarray(labels, dtype=float)
    x = _design(rows, features)
    w = np.zeros(x.shape[1] + 1)
    ones = np.ones((x.shape[0], 1))
    z = np.hstack([ones, x])
    ridge = np.eye(z.shape[1]) * l2
    ridge[0, 0] = 0.0
    for _ in range(25):
        p = 1.0 / (1.0 + np.exp(-np.clip(z @ w, -30, 30)))
        p = np.clip(p, 1e-6, 1 - 1e-6)
        s = p * (1.0 - p)
        grad = z.T @ (p - y) + ridge @ w
        hess = z.T @ (z * s[:, None]) + ridge
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        w = w - step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return FittedGLM(coef=w[1:], intercept=float(w[0]), features=features, l2=l2)


def recover_edge(glm: FittedGLM, row: dict, p_lgb: float, threshold: float = 0.08) -> dict:
    p_glm = glm.predict(row)
    gap = abs(p_glm - float(p_lgb))
    return {
        "p_glm": p_glm,
        "lgb_glm_gap": gap,
        "glm_recovers_lgb": gap <= threshold,
        "note": "LGB interaction does not replicate" if gap > threshold else "GLM tracks LGB",
    }
