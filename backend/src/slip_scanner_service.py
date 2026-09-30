"""Structured scanner service boundary.

The web/API layer should call this service with a resolved event intelligence row.
No OCR/upload is required.
"""
from __future__ import annotations
from typing import Mapping
from ks1.slip_scanner_adapter import ScannerInputUnavailable, build_ks1_moneyline_scan

SUPPORTED={("MLB","moneyline")}

def scan_selection(body: Mapping, intelligence_row: Mapping) -> dict:
    sport=str(body.get("sport") or "").upper()
    market_type=str(body.get("market_type") or body.get("marketType") or "").lower()
    selection=str(body.get("selection") or "").strip()
    if not selection:
        return {"ok":False,"status":400,"error":"selection_required"}
    if (sport,market_type) not in SUPPORTED:
        return {"ok":False,"status":400,"error":"scanner_market_not_supported","sport":sport,"market_type":market_type}
    try:
        assessment=build_ks1_moneyline_scan(
            intelligence_row,selection=selection,
            selected_price_american=body.get("selected_price_american") or body.get("selectedPriceAmerican"),
            best_price_american=body.get("best_price_american") or body.get("bestPriceAmerican"))
    except ScannerInputUnavailable as exc:
        return {"ok":False,"status":503,"error":"scanner_intelligence_not_ready","reason":str(exc)}
    return {"ok":True,"status":200,"assessment":assessment}
