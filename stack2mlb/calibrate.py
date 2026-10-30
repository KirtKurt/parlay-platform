"""Reliability diagrams. This is how we know whether we found patterns."""
from __future__ import annotations

import math

import numpy as np


def reliability(y, p, bins: int = 10) -> list[dict]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if y.shape != p.shape:
        raise ValueError("y and p length mismatch")
    if not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("invalid probabilities")
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo) & (p <= hi)
        n = int(mask.sum())
        rows.append({
            "bucket": f"{lo:.1f}–{hi:.1f}",
            "count": n,
            "mean_p": float(p[mask].mean()) if n else None,
            "hit_rate": float(y[mask].mean()) if n else None,
            "gap": float(p[mask].mean() - y[mask].mean()) if n else None,
        })
    return rows


def ece(rows: list[dict]) -> float:
    total = sum(r["count"] for r in rows)
    if not total:
        return 0.0
    acc = 0.0
    for r in rows:
        if r["count"] and r["gap"] is not None:
            acc += r["count"] * abs(r["gap"])
    return acc / total


def brier(y, p) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2))


def wilson_lower(hits: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 0.0
    phat = hits / n
    den = 1.0 + z * z / n
    centre = phat + z * z / (2.0 * n)
    spread = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - spread) / den)
