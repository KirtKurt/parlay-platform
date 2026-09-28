"""Adapters for existing InQsi sport outputs.

Call these from sport reporting/grading paths. They intentionally do not import
or mutate any sport serving/locking module.
"""
from __future__ import annotations
from typing import Mapping, Sequence
from .favorite_bias import compare_binary, slate_audit

def audit_binary_slate(rows: Sequence[Mapping]) -> dict:
    comparisons=[]
    for r in rows:
        comparisons.append(compare_binary(
            event_id=str(r["event_id"]),
            p_fundamental_home=float(r["p_fundamental_home"]),
            p_market_aware_home=float(r["p_market_aware_home"]),
            p_market_home=float(r["p_market_home"]),
            home=str(r.get("home","home")), away=str(r.get("away","away"))))
    return slate_audit(comparisons)

def audit_soccer_1x2(*, event_id: str, fundamental: Mapping[str,float],
                     market_aware: Mapping[str,float], market: Mapping[str,float]) -> dict:
    keys=("home","draw","away")
    books={}
    for name,values in (("fundamental",fundamental),("market_aware",market_aware),("market",market)):
        row={k:float(values[k]) for k in keys}
        if any(v < 0 or v > 1 for v in row.values()) or abs(sum(row.values())-1)>1e-6:
            raise ValueError(f"{name} 1X2 probabilities must sum to 1")
        books[name]=row
    market_favorite=max(keys,key=books["market"].get)
    fp=max(keys,key=books["fundamental"].get)
    ap=max(keys,key=books["market_aware"].get)
    return {
      "event_id":str(event_id),"market_favorite":market_favorite,
      "fundamental_pick":fp,"market_aware_pick":ap,"market_flip":fp!=ap,
      "market_influence_by_outcome":{k:books["market_aware"][k]-books["fundamental"][k] for k in keys},
      "fundamental_favorite_selected":fp==market_favorite,
      "market_aware_favorite_selected":ap==market_favorite,
      "authority_changed":False,
    }
