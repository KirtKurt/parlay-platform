"""KSS1 independent score-grid diagnostics. Shadow-only."""
from __future__ import annotations
from inqsi_intelligence.adapters import audit_soccer_1x2
from .kss1_engine import blend_with_market
from .kss1_markets import markets_from_grid

def compare_grids(*, event_id, fundamental_grid, market_1x2):
    fundamental=markets_from_grid(fundamental_grid)
    aware_grid=blend_with_market(fundamental_grid,market_1x2)
    aware=markets_from_grid(aware_grid)
    market=dict(market_1x2 or {})
    total=sum(float(market.get(k,0)) for k in ("home","draw","away"))
    if total<=0:
        raise ValueError("usable 1X2 market required")
    market={k:float(market[k])/total for k in ("home","draw","away")}
    return audit_soccer_1x2(
      event_id=str(event_id),
      fundamental={k:fundamental["p_"+k] for k in ("home","draw","away")},
      market_aware={k:aware["p_"+k] for k in ("home","draw","away")},
      market=market)
