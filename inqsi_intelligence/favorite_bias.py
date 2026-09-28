"""Cross-sport independent-vs-market intelligence diagnostics.

Authority-neutral: this module never selects, locks, promotes, or serves a pick.
It only compares already-produced probabilities and summarizes slate bias.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import prod
from typing import Iterable, Mapping, Sequence

@dataclass(frozen=True)
class BinaryComparison:
    event_id: str
    p_fundamental: float
    p_market_aware: float
    p_market_favorite: float
    market_favorite: str
    fundamental_pick: str
    market_aware_pick: str
    market_influence: float
    market_flip: bool
    favorite_selected_fundamental: bool
    favorite_selected_market_aware: bool
    upset_probability_fundamental: float
    upset_probability_market_aware: float

def _p(value: float) -> float:
    value=float(value)
    if not 0.0 <= value <= 1.0:
        raise ValueError("probability must be in [0,1]")
    return value

def compare_binary(*, event_id: str, p_fundamental_home: float,
                   p_market_aware_home: float, p_market_home: float,
                   home: str="home", away: str="away") -> BinaryComparison:
    pf, pa, pm = map(_p,(p_fundamental_home,p_market_aware_home,p_market_home))
    favorite = home if pm >= .5 else away
    f_pick = home if pf >= .5 else away
    a_pick = home if pa >= .5 else away
    dog_p_f = (1-pf) if favorite == home else pf
    dog_p_a = (1-pa) if favorite == home else pa
    return BinaryComparison(
        event_id=str(event_id), p_fundamental=pf, p_market_aware=pa,
        p_market_favorite=max(pm,1-pm), market_favorite=favorite,
        fundamental_pick=f_pick, market_aware_pick=a_pick,
        market_influence=pa-pf, market_flip=f_pick != a_pick,
        favorite_selected_fundamental=f_pick == favorite,
        favorite_selected_market_aware=a_pick == favorite,
        upset_probability_fundamental=dog_p_f,
        upset_probability_market_aware=dog_p_a)

def expected_upsets(rows: Iterable[BinaryComparison], *, use_market_aware: bool=True) -> float:
    attr="upset_probability_market_aware" if use_market_aware else "upset_probability_fundamental"
    return sum(getattr(r,attr) for r in rows)

def upset_count_distribution(rows: Sequence[BinaryComparison], *, use_market_aware: bool=True) -> list[float]:
    """Poisson-binomial distribution; independence is explicit, not hidden."""
    attr="upset_probability_market_aware" if use_market_aware else "upset_probability_fundamental"
    dist=[1.0]
    for row in rows:
        q=_p(getattr(row,attr))
        nxt=[0.0]*(len(dist)+1)
        for k,v in enumerate(dist):
            nxt[k]+=v*(1-q); nxt[k+1]+=v*q
        dist=nxt
    return dist

def slate_audit(rows: Sequence[BinaryComparison]) -> dict:
    n=len(rows)
    return {
        "events": n,
        "fundamental_favorites": sum(r.favorite_selected_fundamental for r in rows),
        "fundamental_underdogs": sum(not r.favorite_selected_fundamental for r in rows),
        "market_aware_favorites": sum(r.favorite_selected_market_aware for r in rows),
        "market_aware_underdogs": sum(not r.favorite_selected_market_aware for r in rows),
        "market_flips": sum(r.market_flip for r in rows),
        "expected_upsets_fundamental": expected_upsets(rows,use_market_aware=False),
        "expected_upsets_market_aware": expected_upsets(rows,use_market_aware=True),
        "upset_count_distribution_market_aware": upset_count_distribution(rows,use_market_aware=True),
        "authority_changed": False,
    }

def active_vs_diagnostic(active_features: Iterable[str], collected_features: Iterable[str]) -> dict:
    active=set(map(str,active_features)); collected=set(map(str,collected_features))
    return {"active_model_signals": sorted(active), "diagnostic_only_signals": sorted(collected-active)}

def assert_fundamentals_market_free(feature_names: Iterable[str],
                                    forbidden_tokens: Sequence[str]=("odds","market","book","vig","spread","moneyline","line_move","consensus")) -> None:
    bad=[f for f in map(str,feature_names) if any(t in f.lower() for t in forbidden_tokens)]
    if bad:
        raise ValueError("market-derived feature(s) in fundamentals pathway: "+", ".join(sorted(bad)))
