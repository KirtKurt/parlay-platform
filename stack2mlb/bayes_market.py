"""Market-as-prior Bayesian update with a hard move cap.

logit(p) = logit(market) + kappa * (logit(signal) - logit(market))
kappa in [0, 1]. The published probability is then clipped so it cannot
travel more than MAX_BAYES_MOVE from the market. That is how 77% dies.
"""
from __future__ import annotations

import math

from stack2mlb import MAX_BAYES_MOVE
from stack2mlb.market import clip01


def logit(p: float) -> float:
    p = clip01(p)
    return math.log(p / (1.0 - p))


def expit(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def update(market_home: float, signal_home: float, kappa: float = 0.25, cap: float = MAX_BAYES_MOVE) -> float:
    if not 0.0 <= kappa <= 1.0:
        raise ValueError("kappa must be in [0, 1]")
    market = clip01(market_home)
    signal = clip01(signal_home)
    raw = expit(logit(market) + kappa * (logit(signal) - logit(market)))
    lo, hi = market - cap, market + cap
    return clip01(min(hi, max(lo, raw)))


def blend_signals(signals: dict[str, float], weights: dict[str, float] | None = None) -> float:
    """Log-odds pool of named signals. Missing weights default equal."""
    names = [k for k, v in signals.items() if v is not None]
    if not names:
        raise ValueError("no signals")
    if weights is None:
        weights = {n: 1.0 for n in names}
    num = den = 0.0
    for n in names:
        w = float(weights.get(n, 0.0))
        if w <= 0:
            continue
        num += w * logit(signals[n])
        den += w
    if den <= 0:
        raise ValueError("weights cancelled")
    return clip01(expit(num / den))
