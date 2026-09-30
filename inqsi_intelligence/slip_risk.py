"""Consumer-facing Slip Scanner risk intelligence.

Authority-neutral. Combines already-produced model/market outputs into structured
risk explanations. Never selects, locks, promotes, deploys, or changes serving authority.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Optional, Sequence
from .favorite_bias import compare_binary

RISK_ORDER=("LOW","LOW_MODERATE","MODERATE","ELEVATED","HIGH")
def _p(v):
    v=float(v)
    if not 0<=v<=1: raise ValueError("probability must be in [0,1]")
    return v
def _clamp(v): return max(0,min(100,int(round(v))))
@dataclass(frozen=True)
class RiskSignal:
    code:str; label:str; severity:str; summary:str; source:str
    confirmed:bool=True; active_model_signal:bool=False
def risk_level(score):
    score=_clamp(score)
    return "HIGH" if score>=75 else "ELEVATED" if score>=60 else "MODERATE" if score>=45 else "LOW_MODERATE" if score>=30 else "LOW"
def _points(s): return {"LOW":4,"LOW_MODERATE":7,"MODERATE":11,"ELEVATED":16,"HIGH":22}.get(str(s).upper(),0)
def _signal(v):
    sev=str(v.get("severity") or "MODERATE").upper()
    if sev not in RISK_ORDER: raise ValueError("unsupported risk severity")
    return RiskSignal(str(v["code"]),str(v.get("label") or v["code"]),sev,str(v["summary"]),str(v.get("source") or "inqsi"),bool(v.get("confirmed",True)),bool(v.get("active_model_signal",False)))

def build_binary_risk_assessment(*,event_id:str,sport:str,home:str,away:str,selection:str,market_type:str,
 p_fundamental_home:float,p_market_aware_home:float,p_market_home:float,signals:Sequence[Mapping]=(),
 books_moved_against:int=0,books_tracked:int=0,movement_minutes:Optional[int]=None,
 best_price_american:Optional[int]=None,selected_price_american:Optional[int]=None,as_of:Optional[str]=None)->dict:
    """Build consumer risk payload. Risk score is diagnostic, not win probability."""
    comp=compare_binary(event_id=event_id,p_fundamental_home=p_fundamental_home,p_market_aware_home=p_market_aware_home,p_market_home=p_market_home,home=home,away=away)
    pf,pa,pm=map(_p,(p_fundamental_home,p_market_aware_home,p_market_home))
    selected_home=str(selection).strip().lower() in {str(home).strip().lower(),"home"}
    sf,sa,sm=(pf,pa,pm) if selected_home else (1-pf,1-pa,1-pm)
    disagreement=sf-sm; influence=sa-sf
    parsed=[_signal(s) for s in signals]
    score=12+min(28,abs(disagreement)*280)+min(16,abs(influence)*240)
    if books_tracked>0 and books_moved_against>0: score+=min(22,(books_moved_against/books_tracked)*22)
    score+=min(32,sum(_points(s.severity) for s in parsed)*.55); score=_clamp(score)
    parts=[]
    if movement_minutes and books_moved_against and books_tracked:
        parts.append(f"Your {selection} selection became materially riskier during the last {int(movement_minutes)} minutes. {books_moved_against} of {books_tracked} tracked sportsbooks moved against {selection}.")
    elif books_moved_against and books_tracked: parts.append(f"{books_moved_against} of {books_tracked} tracked sportsbooks moved against {selection}.")
    if disagreement<=-.03: parts.append("InQsi's independent fundamentals view is less optimistic than the current sportsbook market.")
    elif disagreement>=.03: parts.append("InQsi's independent fundamentals view is more optimistic than the current sportsbook market.")
    confirmed=[s for s in parsed if s.confirmed]; uncertain=[s for s in parsed if not s.confirmed]
    if confirmed: parts.append(confirmed[0].summary)
    if not parts: parts.append("InQsi found no single dominant warning, but the selection still carries measurable pre-bet risk.")
    better=None
    if best_price_american is not None and selected_price_american is not None and best_price_american>selected_price_american:
        better={"available":True,"selected_price_american":int(selected_price_american),"best_price_american":int(best_price_american)}
    return {"schema_version":"slip-risk-v1","authority_changed":False,"event_id":str(event_id),"sport":str(sport),"selection":str(selection),"market_type":str(market_type),"as_of":as_of,
      "risk":{"score":score,"level":risk_level(score)},"consumer_message":" ".join(parts),
      "probabilities":{"fundamentals":round(sf,6),"market_aware":round(sa,6),"sportsbook_implied":round(sm,6),"market_influence":round(influence,6),"fundamentals_vs_market":round(disagreement,6)},
      "market":{"books_moved_against":int(books_moved_against),"books_tracked":int(books_tracked),"movement_minutes":movement_minutes,"market_flip":bool(comp.market_flip),"better_price":better},
      "signals":[asdict(s) for s in parsed],"confirmed_signals":[asdict(s) for s in confirmed],"monitoring_signals":[asdict(s) for s in uncertain],
      "explanation_levels":{"basic":["risk","consumer_message"],"mid":["probabilities","market","confirmed_signals"],"advanced":["signals","monitoring_signals"]}}
def rank_assessments(rows:Iterable[Mapping])->list[dict]:
    return sorted((dict(r) for r in rows),key=lambda r:int(r["risk"]["score"]),reverse=True)
