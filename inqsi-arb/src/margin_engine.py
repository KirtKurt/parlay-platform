"""Per-book sportsbook margin analysis.

Cross-book arb uses the best price per outcome. This module instead measures
each sportsbook's own two-way or three-way juice on a single event+market+line.

Decimal odds d imply probability 1/d. Overround s = sum(1/d_i).
Hold percent = (s - 1) * 100. Edge/margin percent = (1/s - 1) * 100.
Negative hold is a single-book misprice, not a verified multi-book arb.
"""
from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class MarginValidationError(ValueError):
    pass


def american_to_decimal(value: float) -> float:
    american = float(value)
    if not isfinite(american) or american == 0:
        raise MarginValidationError("invalid American odds")
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def decimal_from_quote(raw: Mapping[str, Any]) -> float:
    if raw.get("decimal") is not None and raw.get("decimal") != "":
        decimal = float(raw["decimal"])
        if not isfinite(decimal) or decimal <= 1:
            raise MarginValidationError("invalid decimal odds")
        return decimal
    return american_to_decimal(float(raw["american"]))


def line_bucket(point: Any) -> Tuple[Any, ...]:
    if point is None or point == "":
        return ("none",)
    try:
        value = float(point)
    except (TypeError, ValueError):
        return ("invalid", type(point).__name__, repr(point))
    if not isfinite(value):
        return ("invalid", "nonfinite", repr(point))
    return ("abs", round(abs(value), 4))


def _best_prices(quotes: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for raw in quotes or []:
        outcome = str(raw.get("outcome") or raw.get("name") or "").strip()
        if not outcome:
            continue
        try:
            decimal = decimal_from_quote(raw)
        except (KeyError, TypeError, ValueError, MarginValidationError):
            continue
        american = raw.get("american")
        try:
            american_value = float(american) if american is not None and american != "" else None
        except (TypeError, ValueError):
            american_value = None
        previous = best.get(outcome)
        if previous is None or decimal > float(previous["decimal"]):
            best[outcome] = {
                "outcome": outcome,
                "american": american_value,
                "decimal": decimal,
                "point": raw.get("point"),
                "implied": 1.0 / decimal,
            }
    return best


def book_market_hold(
    quotes: Iterable[Mapping[str, Any]],
    *,
    book: str,
    event: str,
    market: str,
    sport: Optional[str] = None,
    point: Any = None,
    commence_time: Optional[str] = None,
    expected_outcomes: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Hold for one book on one event+market+line, or None if incomplete."""
    wanted_book = str(book or "").strip().lower()
    wanted_line = None if point is None else line_bucket(point)
    filtered = []
    for raw in quotes or []:
        if str(raw.get("book") or "").strip().lower() != wanted_book:
            continue
        if wanted_line is not None and line_bucket(raw.get("point")) != wanted_line:
            continue
        filtered.append(raw)
    prices = _best_prices(filtered)
    if len(prices) < 2:
        return None
    if expected_outcomes:
        needed = {str(name).strip() for name in expected_outcomes if str(name).strip()}
        if needed and not needed <= set(prices):
            return None
    overround = sum(float(row["implied"]) for row in prices.values())
    if overround <= 0 or not isfinite(overround):
        return None
    hold_pct = (overround - 1.0) * 100.0
    margin_pct = (1.0 / overround - 1.0) * 100.0
    point_values = [row.get("point") for row in prices.values() if row.get("point") not in (None, "")]
    return {
        "book": wanted_book,
        "event": event,
        "market": str(market or "").strip().lower(),
        "sport": sport,
        "commence_time": commence_time,
        "point": point if point is not None else (point_values[0] if point_values else None),
        "point_bucket": wanted_line or line_bucket(point_values[0] if point_values else None),
        "n_outcomes": len(prices),
        "n_quotes": len(filtered),
        "complete": True,
        "sum_implied": round(overround, 8),
        "hold_pct": round(hold_pct, 4),
        "margin_pct": round(margin_pct, 4),
        "negative_hold": hold_pct < 0,
        "prices": [
            {
                "outcome": row["outcome"],
                "american": row["american"],
                "decimal": round(float(row["decimal"]), 6),
                "point": row.get("point"),
            }
            for row in sorted(prices.values(), key=lambda item: item["outcome"])
        ],
    }


def _event_rows(event: Mapping[str, Any], *, sport: Optional[str] = None) -> Tuple[List[Dict[str, Any]], int]:
    quotes = list(event.get("quotes") or [])
    expected = event.get("expected_outcomes")
    groups: Dict[Tuple[Any, ...], List[Mapping[str, Any]]] = {}
    for raw in quotes:
        book = str(raw.get("book") or "").strip().lower()
        if not book:
            continue
        key = (book, line_bucket(raw.get("point")))
        groups.setdefault(key, []).append(raw)
    rows: List[Dict[str, Any]] = []
    incomplete = 0
    for (book, bucket), grouped in groups.items():
        point = None
        for raw in grouped:
            if raw.get("point") not in (None, ""):
                point = raw.get("point")
                break
        row = book_market_hold(
            grouped,
            book=book,
            event=str(event.get("event") or event.get("event_id") or ""),
            market=str(event.get("market") or ""),
            sport=str(event.get("sport") or sport or "") or None,
            point=None if bucket == ("none",) else point,
            commence_time=event.get("commence_time"),
            expected_outcomes=expected,
        )
        if row is None:
            incomplete += 1
            continue
        rows.append(row)
    return rows, incomplete


def _book_summary(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["book"]), []).append(row)
    summaries = []
    for book, items in grouped.items():
        holds = [float(item["hold_pct"]) for item in items]
        summaries.append({
            "book": book,
            "n_markets": len(items),
            "mean_hold_pct": round(sum(holds) / len(holds), 4),
            "median_hold_pct": round(float(median(holds)), 4),
            "min_hold_pct": round(min(holds), 4),
            "max_hold_pct": round(max(holds), 4),
            "n_negative_hold": sum(1 for item in items if item.get("negative_hold")),
        })
    summaries.sort(key=lambda item: (item["mean_hold_pct"], item["book"]))
    return summaries


def _family_summary(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("market") or "unknown"), []).append(row)
    summaries = []
    for market, items in grouped.items():
        holds = [float(item["hold_pct"]) for item in items]
        summaries.append({
            "market": market,
            "n_book_markets": len(items),
            "n_books": len({item["book"] for item in items}),
            "mean_hold_pct": round(sum(holds) / len(holds), 4),
            "median_hold_pct": round(float(median(holds)), 4),
        })
    summaries.sort(key=lambda item: item["market"])
    return summaries


def analyze_events(events: Iterable[Mapping[str, Any]], *, sport: Optional[str] = None) -> Dict[str, Any]:
    markets: List[Dict[str, Any]] = []
    incomplete = 0
    for event in events or []:
        rows, skipped = _event_rows(event, sport=sport)
        markets.extend(rows)
        incomplete += skipped
    negative = [row for row in markets if row.get("negative_hold")]
    tightest = sorted(markets, key=lambda row: (row["hold_pct"], row["book"], row["event"]))[:25]
    juiciest = sorted(markets, key=lambda row: (-row["hold_pct"], row["book"], row["event"]))[:25]
    return {
        "ok": True,
        "sport": sport,
        "n_events": len(list(events or [])),
        "n_complete": len(markets),
        "n_incomplete": incomplete,
        "n_negative_hold": len(negative),
        "books": _book_summary(markets),
        "market_families": _family_summary(markets),
        "tightest": tightest,
        "juiciest": juiciest,
        "negative_hold": negative[:25],
        "policy": (
            "Hold is each sportsbook's own overround on one event+market+line. "
            "Negative hold is a single-book misprice, not a settlement-verified arb."
        ),
    }


def analyze_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return analyze_events(payload.get("events") or [], sport=payload.get("sport"))


def analyze_snapshots(snapshots: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    combined: List[Mapping[str, Any]] = []
    sports: List[str] = []
    for snap in snapshots or []:
        sport = str(snap.get("sport") or "") or None
        if sport:
            sports.append(sport)
        for event in snap.get("events") or []:
            row = dict(event)
            if sport and not row.get("sport"):
                row["sport"] = sport
            combined.append(row)
    report = analyze_events(combined, sport=sports[0] if len(set(sports)) == 1 else ("all" if sports else None))
    report["n_snapshots"] = len(list(snapshots or []))
    report["sports"] = sorted(set(sports))
    return report
