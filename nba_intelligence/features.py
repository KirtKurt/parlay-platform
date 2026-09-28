"""Leakage-safe rolling feature primitives."""
from collections import defaultdict
WINDOWS=(3,5,10,20)

def prior_shrink(mean: float, n: int, prior: float, strength: float=5.0) -> float:
    if n < 0 or strength < 0: raise ValueError("invalid shrinkage")
    return (mean*n + prior*strength)/(n+strength) if n+strength else prior

def rolling_before(rows, *, game_time, key, value):
    """Rows must contain occurred_at; observations at/after game_time are excluded."""
    prior=[r for r in rows if r["occurred_at"] < game_time and r.get(value) is not None]
    prior.sort(key=lambda r:r["occurred_at"])
    out={}
    for n in WINDOWS:
        sample=prior[-n:]
        out[f"{key}_last_{n}"]=None if not sample else sum(float(r[value]) for r in sample)/len(sample)
    out[f"{key}_season_to_date"]=None if not prior else sum(float(r[value]) for r in prior)/len(prior)
    return out

def assert_feature_times(provenances, prediction_time):
    for p in provenances: p.assert_available(prediction_time)
