"""Surebet scanner. No I/O. Never places a bet.

A market is an arb when the best available decimal price on every mutually
exclusive outcome satisfies sum(1/odds) < 1. Stakes are proportional so the
payout is the same no matter which outcome wins.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def american_to_decimal(american: float) -> float:
    a = float(american)
    if a == 0:
        raise ValueError("American odds cannot be 0")
    return 1.0 + a / 100.0 if a > 0 else 1.0 + 100.0 / abs(a)


def implied_prob(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("Decimal odds must be > 1")
    return 1.0 / decimal_odds


def scan_market(
    *,
    market_id: str,
    event: str,
    market: str,
    quotes: Iterable[Dict[str, Any]],
    bankroll: float = 1000.0,
    commence_time: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Best price per outcome, then test Σ 1/o < 1."""
    best: Dict[str, Dict[str, Any]] = {}
    n_quotes = 0
    books = set()
    for raw in quotes or []:
        name = str(raw.get("outcome") or "").strip()
        if not name:
            continue
        try:
            american = float(raw["american"])
            dec = american_to_decimal(american)
        except (KeyError, TypeError, ValueError):
            continue
        n_quotes += 1
        book = str(raw.get("book") or "unknown")
        books.add(book)
        prev = best.get(name)
        if prev is None or dec > prev["decimal"]:
            best[name] = {
                "outcome": name,
                "book": book,
                "american": int(american) if american == int(american) else american,
                "decimal": dec,
                "implied": implied_prob(dec),
            }
    if len(best) < 2:
        return None
    s = sum(v["implied"] for v in best.values())
    margin = (1.0 / s - 1.0) if s > 0 else -1.0
    bankroll = float(bankroll or 1000.0)
    stakes = {name: round(bankroll * v["implied"] / s, 2) for name, v in best.items()}
    payout = bankroll / s if s > 0 else 0.0
    return {
        "market_id": market_id,
        "event": event,
        "market": market,
        "commence_time": commence_time,
        "arb": s < 1.0,
        "sum_implied": round(s, 6),
        "margin_pct": round(margin * 100.0, 3),
        "hold_pct": round((s - 1.0) * 100.0, 3),
        "best": {
            k: {
                "book": v["book"],
                "american": v["american"],
                "decimal": round(v["decimal"], 4),
            }
            for k, v in best.items()
        },
        "stakes": stakes,
        "guaranteed_payout": round(payout, 2) if s < 1.0 else None,
        "guaranteed_profit": round(payout - bankroll, 2) if s < 1.0 else None,
        "bankroll": bankroll,
        "outcomes": list(best.keys()),
        "n_quotes": n_quotes,
        "n_books": len(books),
        "context": context or {},
    }


def scan_all(payload: Dict[str, Any]) -> Dict[str, Any]:
    bankroll = float(payload.get("bankroll") or 1000.0)
    hits: List[Dict[str, Any]] = []
    near: List[Dict[str, Any]] = []
    skipped = 0
    for ev in payload.get("events") or []:
        quotes = ev.get("quotes") or []
        row = scan_market(
            market_id=str(ev.get("id") or ev.get("market_id") or ev.get("event") or ""),
            event=str(ev.get("event") or ev.get("id") or ""),
            market=str(ev.get("market") or "h2h"),
            quotes=quotes,
            bankroll=bankroll,
            commence_time=ev.get("commence_time"),
            context=ev.get("context") or {},
        )
        if row is None:
            skipped += 1
            continue
        (hits if row["arb"] else near).append(row)
    hits.sort(key=lambda r: r["margin_pct"], reverse=True)
    near.sort(key=lambda r: r["sum_implied"])
    return {
        "ok": True,
        "places_bets": False,
        "bankroll": bankroll,
        "n_events": len(payload.get("events") or []),
        "n_markets": len(hits) + len(near),
        "n_arbs": len(hits),
        "n_skipped": skipped,
        "hits": hits,
        "near": near[:25],
        "best_margin_pct": hits[0]["margin_pct"] if hits else None,
        "tightest_hold_pct": near[0]["hold_pct"] if near else None,
    }
