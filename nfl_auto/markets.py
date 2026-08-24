"""Bookmaker consensus and de-vigging for NFL featured markets."""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .canonical import normalize_team


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def implied_probability(decimal_price: Any) -> float | None:
    price = _finite(decimal_price)
    if price is None or price <= 1.0:
        return None
    return 1.0 / price


def devig(prices: Sequence[Any]) -> list[float] | None:
    probabilities = [implied_probability(price) for price in prices]
    if any(value is None for value in probabilities):
        return None
    values = [float(value) for value in probabilities if value is not None]
    total = sum(values)
    if total <= 0:
        return None
    return [value / total for value in values]


def _median(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return float(statistics.median(rows)) if rows else None


def _mean(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if math.isfinite(float(value))]
    return sum(rows) / len(rows) if rows else None


def _stdev(values: Sequence[float]) -> float:
    return float(statistics.pstdev(values)) if len(values) > 1 else 0.0


def _market(bookmaker: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    for market in bookmaker.get("markets") or []:
        if isinstance(market, Mapping) and str(market.get("key")) == key:
            return market
    return None


def _outcomes(market: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not market:
        return []
    return [row for row in market.get("outcomes") or [] if isinstance(row, Mapping)]


def _named_outcome(outcomes: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    target = str(name).strip().lower()
    for row in outcomes:
        if str(row.get("name") or "").strip().lower() == target:
            return row
    return None


def moneyline_consensus(event: Mapping[str, Any]) -> dict[str, Any]:
    home = normalize_team(event.get("home_team"))
    away = normalize_team(event.get("away_team"))
    home_values: list[float] = []
    books: list[str] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping):
            continue
        outcomes = _outcomes(_market(book, "h2h"))
        by_team: dict[str, Mapping[str, Any]] = {}
        for outcome in outcomes:
            try:
                by_team[normalize_team(outcome.get("name"))] = outcome
            except ValueError:
                continue
        if home not in by_team or away not in by_team:
            continue
        probs = devig([by_team[home].get("price"), by_team[away].get("price")])
        if not probs:
            continue
        home_values.append(probs[0])
        books.append(str(book.get("key") or book.get("title") or "unknown"))
    return {
        "home_probability": _median(home_values),
        "bookmaker_count": len(home_values),
        "dispersion": _stdev(home_values),
        "bookmakers": sorted(set(books)),
    }


def spread_consensus(event: Mapping[str, Any]) -> dict[str, Any]:
    home = normalize_team(event.get("home_team"))
    away = normalize_team(event.get("away_team"))
    candidates: list[tuple[float, float, str]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping):
            continue
        outcomes = _outcomes(_market(book, "spreads"))
        by_team: dict[str, Mapping[str, Any]] = {}
        for outcome in outcomes:
            try:
                by_team[normalize_team(outcome.get("name"))] = outcome
            except ValueError:
                continue
        home_row = by_team.get(home)
        away_row = by_team.get(away)
        if not home_row or not away_row:
            continue
        home_point = _finite(home_row.get("point"))
        away_point = _finite(away_row.get("point"))
        if home_point is None or away_point is None or abs(home_point + away_point) > 0.26:
            continue
        probs = devig([home_row.get("price"), away_row.get("price")])
        if not probs:
            continue
        candidates.append(
            (home_point, probs[0], str(book.get("key") or book.get("title") or "unknown"))
        )
    representative = _median(row[0] for row in candidates)
    if representative is None:
        return {
            "home_line": None,
            "home_probability": None,
            "bookmaker_count": 0,
            "dispersion": 0.0,
            "bookmakers": [],
        }
    # Half-point tolerance preserves enough books while avoiding a probability
    # blend across materially different spread numbers.
    aligned = [row for row in candidates if abs(row[0] - representative) <= 0.51]
    probabilities = [row[1] for row in aligned]
    return {
        "home_line": representative,
        "home_probability": _median(probabilities),
        "bookmaker_count": len(aligned),
        "dispersion": _stdev(probabilities),
        "bookmakers": sorted({row[2] for row in aligned}),
    }


def total_consensus(event: Mapping[str, Any]) -> dict[str, Any]:
    candidates: list[tuple[float, float, str]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping):
            continue
        outcomes = _outcomes(_market(book, "totals"))
        over = _named_outcome(outcomes, "over")
        under = _named_outcome(outcomes, "under")
        if not over or not under:
            continue
        over_point = _finite(over.get("point"))
        under_point = _finite(under.get("point"))
        if over_point is None or under_point is None or abs(over_point - under_point) > 0.26:
            continue
        probs = devig([over.get("price"), under.get("price")])
        if not probs:
            continue
        candidates.append(
            (over_point, probs[0], str(book.get("key") or book.get("title") or "unknown"))
        )
    representative = _median(row[0] for row in candidates)
    if representative is None:
        return {
            "total_line": None,
            "over_probability": None,
            "bookmaker_count": 0,
            "dispersion": 0.0,
            "bookmakers": [],
        }
    aligned = [row for row in candidates if abs(row[0] - representative) <= 0.51]
    probabilities = [row[1] for row in aligned]
    return {
        "total_line": representative,
        "over_probability": _median(probabilities),
        "bookmaker_count": len(aligned),
        "dispersion": _stdev(probabilities),
        "bookmakers": sorted({row[2] for row in aligned}),
    }


def snapshot_consensus(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "moneyline": moneyline_consensus(event),
        "spread": spread_consensus(event),
        "total": total_consensus(event),
    }


def consensus_is_eligible(consensus: Mapping[str, Any], market: str, min_bookmakers: int) -> bool:
    row = consensus.get(market)
    if not isinstance(row, Mapping):
        return False
    count = int(row.get("bookmaker_count") or 0)
    if count < min_bookmakers:
        return False
    required = {
        "moneyline": ("home_probability",),
        "spread": ("home_line", "home_probability"),
        "total": ("total_line", "over_probability"),
    }[market]
    return all(row.get(field) is not None for field in required)
