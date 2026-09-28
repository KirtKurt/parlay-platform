"""Odds conversion, no-vig consensus, and market snapshots."""
from math import isfinite

def american_to_prob(odds: float) -> float:
    if not isfinite(odds) or odds == 0:
        raise ValueError("invalid American odds")
    return 100.0/(odds+100.0) if odds > 0 else (-odds)/((-odds)+100.0)

def devig_two_way(a: float, b: float) -> tuple[float,float]:
    pa,pb=american_to_prob(a),american_to_prob(b)
    z=pa+pb
    if z <= 0: raise ValueError("invalid market")
    return pa/z,pb/z

def consensus_no_vig(pairs):
    vals=[devig_two_way(a,b) for a,b in pairs]
    if not vals: return None
    return tuple(sum(x[i] for x in vals)/len(vals) for i in (0,1))
