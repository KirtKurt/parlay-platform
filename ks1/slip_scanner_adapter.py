"""KS1 -> Slip Scanner adapter.

Fail-closed: never substitutes market probability or active p_home for a missing
independent fundamentals probability.
"""
from __future__ import annotations
from typing import Mapping, Sequence
from inqsi_intelligence.slip_risk import build_binary_risk_assessment

class ScannerInputUnavailable(ValueError):
    pass

def _required(row: Mapping, key: str):
    value=row.get(key)
    if value is None or value == "":
        raise ScannerInputUnavailable(f"missing_required_scanner_input:{key}")
    return value

def _signal_rows(row: Mapping) -> list[dict]:
    out=[]
    for item in row.get("scanner_signals") or ():
        if not isinstance(item, Mapping):
            continue
        if not item.get("code") or not item.get("summary"):
            continue
        out.append(dict(item))
    return out

def build_ks1_moneyline_scan(row: Mapping, *, selection: str, selected_price_american=None, best_price_american=None) -> dict:
    """Adapt a prediction-time KS1 row without changing or recomputing KS1."""
    event_id=str(row.get("game_id") or row.get("event_id") or "")
    if not event_id:
        raise ScannerInputUnavailable("missing_required_scanner_input:game_id")
    home=str(_required(row,"home_team")); away=str(_required(row,"away_team"))
    pf=float(_required(row,"p_fundamental_home"))
    pa=float(_required(row,"p_home"))
    pm=float(_required(row,"market_home_prob"))
    return build_binary_risk_assessment(
        event_id=event_id,sport="MLB",home=home,away=away,selection=selection,market_type="moneyline",
        p_fundamental_home=pf,p_market_aware_home=pa,p_market_home=pm,
        signals=_signal_rows(row),
        books_moved_against=int(row.get("books_moved_against") or 0),
        books_tracked=int(row.get("books_tracked") or 0),
        movement_minutes=int(row["movement_minutes"]) if row.get("movement_minutes") is not None else None,
        selected_price_american=selected_price_american,best_price_american=best_price_american,
        as_of=row.get("as_of"),
    )
