"""Read-only Poisson / Skellam bridge.

Does not refit KS1 dual-Poisson. Turns frozen lambdas into:
  - P(home win) with 50/50 ties (same contract as ks1.poisson.home_probability)
  - residual features an LGB *challenger* can consume later
"""
from __future__ import annotations

import math

from stack2mlb.market import clip01


def _poi(k: int, mu: float) -> float:
    if k < 0:
        return 0.0
    return math.exp(k * math.log(mu) - mu - math.lgamma(k + 1))


def home_probability(lambda_home: float, lambda_away: float) -> tuple[float, float]:
    lh, la = float(lambda_home), float(lambda_away)
    if not (lh > 0 and la > 0 and math.isfinite(lh) and math.isfinite(la)):
        raise ValueError("Poisson rates must be finite and positive")
    try:
        from scipy.stats import skellam
        tie = float(skellam.pmf(0, lh, la))
        p = float(skellam.sf(0, lh, la) + 0.5 * tie)
    except Exception:
        p_home_strict = 0.0
        tie = 0.0
        cap = 30
        for h in range(0, cap + 1):
            for a in range(0, cap + 1):
                mass = _poi(h, lh) * _poi(a, la)
                if h > a:
                    p_home_strict += mass
                elif h == a:
                    tie += mass
        p = p_home_strict + 0.5 * tie
    p = clip01(p)
    return p, clip01(tie)


def residual_features(
    lambda_home: float,
    lambda_away: float,
    market_home: float | None = None,
    p_lgb: float | None = None,
) -> dict:
    """Features that let a future LGB explain what Poisson already does not."""
    p_poi, tie = home_probability(lambda_home, lambda_away)
    lh, la = float(lambda_home), float(lambda_away)
    row = {
        "lambda_home": lh,
        "lambda_away": la,
        "lambda_diff": lh - la,
        "log_lambda_ratio": math.log(lh) - math.log(la),
        "proj_total": lh + la,
        "p_poisson": p_poi,
        "poisson_tie_prob": tie,
    }
    if market_home is not None:
        m = clip01(market_home)
        row["poisson_minus_market"] = p_poi - m
        row["logit_poisson_minus_market"] = _logit(p_poi) - _logit(m)
    if p_lgb is not None:
        g = clip01(p_lgb)
        row["lgb_minus_poisson"] = g - p_poi
        row["logit_lgb_minus_poisson"] = _logit(g) - _logit(p_poi)
    return row


def _logit(p: float) -> float:
    p = clip01(p)
    return math.log(p / (1.0 - p))
