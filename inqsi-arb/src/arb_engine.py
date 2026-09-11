"""Deterministic N-way arbitrage engine for Inqsi.

The engine has no network or AWS dependencies. It evaluates normalized quote
sets and explicitly separates mathematical detection from settlement-verified
arbitrage qualification. Mathematical opportunity detection is never enough to
label an opportunity a verified arb: settlement rules must be COMPATIBLE.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional


class ArbValidationError(ValueError):
    pass


def american_to_decimal(value: float) -> float:
    a = float(value)
    if not isfinite(a) or a == 0:
        raise ArbValidationError("invalid American odds")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def decimal_to_american(value: float) -> float:
    d = float(value)
    if not isfinite(d) or d <= 1:
        raise ArbValidationError("invalid decimal odds")
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def _quote_decimal(raw: Mapping[str, Any]) -> float:
    if raw.get("decimal") is not None:
        d = float(raw["decimal"])
        if not isfinite(d) or d <= 1:
            raise ArbValidationError("invalid decimal odds")
        return d
    return american_to_decimal(float(raw["american"]))


def _net_decimal(decimal_odds: float, commission_rate: float) -> float:
    c = float(commission_rate or 0.0)
    if c < 0 or c >= 1:
        raise ArbValidationError("commission must be in [0, 1)")
    return 1.0 + (decimal_odds - 1.0) * (1.0 - c)


def scan_market(*, market_id: str, event: str, market: str, quotes: Iterable[Mapping[str, Any]],
                bankroll: float = 1000.0, expected_outcomes: Optional[Iterable[str]] = None,
                rules_status: str = "unknown", context: Optional[Mapping[str, Any]] = None,
                commence_time: Optional[str] = None) -> Optional[Dict[str, Any]]:
    bankroll = float(bankroll)
    if not isfinite(bankroll) or bankroll <= 0:
        raise ArbValidationError("bankroll must be positive")
    expected = [str(x).strip() for x in (expected_outcomes or []) if str(x).strip()]
    expected_set = set(expected)
    best: Dict[str, Dict[str, Any]] = {}
    seen_books = set()
    valid_quotes = 0
    for raw in quotes or []:
        outcome = str(raw.get("outcome") or "").strip(); book = str(raw.get("book") or "").strip()
        if not outcome or not book: continue
        try:
            d = _quote_decimal(raw); net_d = _net_decimal(d, float(raw.get("commission_rate") or 0.0))
        except (TypeError, ValueError, ArbValidationError):
            continue
        valid_quotes += 1; seen_books.add(book)
        candidate = {"outcome": outcome, "book": book,
                     "american": raw.get("american") if raw.get("american") is not None else round(decimal_to_american(d), 2),
                     "decimal": d, "net_decimal": net_d, "commission_rate": float(raw.get("commission_rate") or 0.0),
                     "provider": raw.get("provider"), "last_update": raw.get("last_update"), "link": raw.get("link"), "limit": raw.get("limit")}
        if best.get(outcome) is None or net_d > best[outcome]["net_decimal"]: best[outcome] = candidate
    if expected_set:
        missing = sorted(expected_set - set(best)); extra = sorted(set(best) - expected_set); complete = not missing and not extra
    else:
        missing, extra = [], []; complete = len(best) >= 2; expected_set = set(best)
    if len(best) < 2: return None
    implied_sum = sum(1.0 / best[o]["net_decimal"] for o in sorted(expected_set) if o in best)
    math_arb = bool(complete and implied_sum < 1.0)
    normalized_rules_status = str(rules_status or "unknown").strip().lower()
    rules_compatible = normalized_rules_status == "compatible"
    is_arb = bool(math_arb and rules_compatible)
    theoretical_return = (1.0 / implied_sum - 1.0) if implied_sum > 0 else -1.0
    stake_rows: List[Dict[str, Any]] = []
    if complete and implied_sum > 0:
        unrounded = {o: bankroll * (1.0 / best[o]["net_decimal"]) / implied_sum for o in expected_set}
        rounded = {o: round(v, 2) for o, v in unrounded.items()}; delta = round(bankroll - sum(rounded.values()), 2)
        if rounded and delta:
            largest = max(rounded, key=rounded.get); rounded[largest] = round(rounded[largest] + delta, 2)
        for outcome in sorted(expected_set):
            q = best[outcome]; stake = rounded[outcome]; payout = stake * q["net_decimal"]
            stake_rows.append({"outcome": outcome, "book": q["book"], "american": q["american"],
                               "decimal": round(q["decimal"], 6), "net_decimal": round(q["net_decimal"], 6),
                               "stake": stake, "payout_if_wins": round(payout, 2), "profit_if_wins": round(payout - bankroll, 2),
                               "last_update": q.get("last_update"), "provider": q.get("provider"), "link": q.get("link"), "limit": q.get("limit")})
    min_profit = min((r["profit_if_wins"] for r in stake_rows), default=None)
    min_payout = min((r["payout_if_wins"] for r in stake_rows), default=None)
    validation = {
        "outcome_coverage": "complete" if complete else "incomplete",
        "rules_status": normalized_rules_status,
        "rules_compatible": rules_compatible,
        "missing_outcomes": missing,
        "extra_outcomes": extra,
    }
    if math_arb and not rules_compatible:
        validation["qualification_reason"] = "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    return {"market_id": market_id, "event": event, "market": market, "commence_time": commence_time,
            "math_arb": math_arb, "arb": is_arb,
            "validation": validation,
            "sum_implied": round(implied_sum, 8), "margin_pct": round(theoretical_return * 100.0, 4),
            "hold_pct": round((implied_sum - 1.0) * 100.0, 4), "bankroll": round(bankroll, 2), "legs": stake_rows,
            "minimum_payout": min_payout if math_arb else None, "minimum_profit": min_profit if math_arb else None,
            "n_quotes": valid_quotes, "n_books": len(seen_books), "outcomes": sorted(expected_set), "context": dict(context or {})}


def scan_all(payload: Mapping[str, Any]) -> Dict[str, Any]:
    bankroll = float(payload.get("bankroll") or 1000.0)
    hits: List[Dict[str, Any]] = []; detected: List[Dict[str, Any]] = []; near: List[Dict[str, Any]] = []; rejected: List[Dict[str, Any]] = []
    for item in payload.get("events") or []:
        row = scan_market(market_id=str(item.get("id") or item.get("market_id") or ""), event=str(item.get("event") or ""),
                          market=str(item.get("market") or "unknown"), quotes=item.get("quotes") or [], bankroll=bankroll,
                          expected_outcomes=item.get("expected_outcomes"), rules_status=str(item.get("rules_status") or "unknown"),
                          context=item.get("context") or {}, commence_time=item.get("commence_time"))
        if row is None: continue
        rules_status = str(row["validation"]["rules_status"]).lower()
        invalid = row["validation"]["outcome_coverage"] != "complete" or rules_status == "incompatible"
        if invalid: rejected.append(row)
        elif row["arb"]: hits.append(row)
        elif row["math_arb"]: detected.append(row)
        else: near.append(row)
    key = lambda r: (r["minimum_profit"] or -10**9, r["margin_pct"])
    hits.sort(key=key, reverse=True); detected.sort(key=key, reverse=True); near.sort(key=lambda r: r["sum_implied"])
    return {"ok": True, "places_bets": False, "bankroll": bankroll,
            "n_markets": len(hits)+len(detected)+len(near)+len(rejected), "n_arbs": len(hits),
            "n_detected_unverified": len(detected), "n_rejected": len(rejected),
            "hits": hits, "detected_unverified": detected[:100], "near": near[:100], "rejected": rejected[:100]}
