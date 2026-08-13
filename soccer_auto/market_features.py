"""Leakage-safe market features for the initial soccer 1X2 model."""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence


OUTCOMES = ("home", "draw", "away")
FEATURE_SCHEMA_VERSION = "soccer-auto-market-features-v2"
LEAGUE_HASH_BUCKETS = 16
MARKET_HASH_BUCKETS = 32


def _name_to_side(name: str, *, home_team: str, away_team: str) -> str | None:
    value = str(name or "").casefold().strip()
    if value == str(home_team or "").casefold().strip():
        return "home"
    if value == str(away_team or "").casefold().strip():
        return "away"
    if value in {"draw", "tie"}:
        return "draw"
    return None


def devig_three_way(
    outcomes: Sequence[Mapping[str, Any]], *, home_team: str, away_team: str
) -> dict[str, float] | None:
    raw: dict[str, float] = {}
    for outcome in outcomes:
        side = _name_to_side(outcome.get("name"), home_team=home_team, away_team=away_team)
        try:
            price = float(outcome.get("price"))
        except (TypeError, ValueError):
            continue
        if side and price > 1.0:
            raw[side] = 1.0 / price
    if set(raw) != set(OUTCOMES):
        return None
    total = sum(raw.values())
    if total <= 0:
        return None
    return {side: raw[side] / total for side in OUTCOMES}


def _markets(book: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    value = book.get("markets") or []
    if isinstance(value, Mapping):
        for key, outcomes in value.items():
            yield {"key": key, "outcomes": outcomes}
    else:
        yield from value


def book_three_way_probabilities(event: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for book in event.get("bookmakers") or []:
        for market in _markets(book):
            if market.get("key") not in {"h2h", "h2h_3_way"}:
                continue
            probabilities = devig_three_way(
                market.get("outcomes") or [],
                home_team=str(event.get("home_team") or ""),
                away_team=str(event.get("away_team") or ""),
            )
            if probabilities:
                result[str(book.get("key"))] = probabilities
                break
    return result


def consensus_three_way(event: Mapping[str, Any]) -> dict[str, Any] | None:
    by_book = book_three_way_probabilities(event)
    if not by_book:
        return None
    values = {side: [row[side] for row in by_book.values()] for side in OUTCOMES}
    mean = {side: statistics.fmean(values[side]) for side in OUTCOMES}
    total = sum(mean.values())
    mean = {side: mean[side] / total for side in OUTCOMES}
    dispersion = {
        side: statistics.pstdev(values[side]) if len(values[side]) > 1 else 0.0
        for side in OUTCOMES
    }
    return {"probabilities": mean, "dispersion": dispersion, "book_count": len(by_book), "by_book": by_book}


def _league_bucket(sport_key: str) -> int:
    raw = hashlib.sha256(sport_key.encode("utf-8")).digest()
    return int.from_bytes(raw[:4], "big") % LEAGUE_HASH_BUCKETS


def _market_bucket(market_key: str) -> int:
    raw = hashlib.sha256(market_key.encode("utf-8")).digest()
    return int.from_bytes(raw[:4], "big") % MARKET_HASH_BUCKETS


def _market_points(event: Mapping[str, Any], market_key: str) -> list[float]:
    rows: list[float] = []
    for book in event.get("bookmakers") or []:
        for market in _markets(book):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes") or []:
                try:
                    point = float(outcome["point"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(point):
                    rows.append(point)
    return rows


def _all_market_summary(event: Mapping[str, Any]) -> dict[str, Any]:
    keys = set()
    pairs = 0
    outcomes = 0
    log_prices = []
    points = []
    player_pairs = 0
    corner_pairs = 0
    card_pairs = 0
    period_pairs = 0
    buckets = [0.0] * MARKET_HASH_BUCKETS
    for book in event.get("bookmakers") or []:
        for market in _markets(book):
            key = str(market.get("key") or "").strip()
            if not key:
                continue
            keys.add(key)
            pairs += 1
            bucket = _market_bucket(key)
            market_outcomes = market.get("outcomes") or []
            buckets[bucket] += 1.0 + math.log1p(len(market_outcomes))
            player_pairs += int(key.startswith("player_"))
            corner_pairs += int("corner" in key)
            card_pairs += int("card" in key)
            period_pairs += int(key.endswith("_h1") or key.endswith("_h2") or "halftime" in key)
            for outcome in market_outcomes:
                outcomes += 1
                try:
                    price = float(outcome.get("price"))
                    if price > 1.0 and math.isfinite(price):
                        log_prices.append(math.log(price))
                except (TypeError, ValueError):
                    pass
                try:
                    point = float(outcome.get("point"))
                    if math.isfinite(point):
                        points.append(point)
                except (TypeError, ValueError):
                    pass
    return {
        "market_count": len(keys),
        "pairs": pairs,
        "outcomes": outcomes,
        "price_mean": statistics.fmean(log_prices) if log_prices else 0.0,
        "price_std": statistics.pstdev(log_prices) if len(log_prices) > 1 else 0.0,
        "point_abs_mean": statistics.fmean(abs(value) for value in points) if points else 0.0,
        "point_std": statistics.pstdev(points) if len(points) > 1 else 0.0,
        "player_pairs": player_pairs,
        "corner_pairs": corner_pairs,
        "card_pairs": card_pairs,
        "period_pairs": period_pairs,
        "buckets": buckets,
    }


BASE_FEATURE_NAMES = (
    "prior_home", "prior_draw", "prior_away",
    "dispersion_home", "dispersion_draw", "dispersion_away",
    "movement_home", "movement_draw", "movement_away",
    "book_count_log", "hours_to_start_log", "totals_line", "spread_abs",
    "all_market_count_log", "all_book_market_pair_count_log", "all_outcome_count_log",
    "all_log_price_mean", "all_log_price_std", "all_point_abs_mean", "all_point_std",
    "player_market_pair_count_log", "corner_market_pair_count_log",
    "card_market_pair_count_log", "period_market_pair_count_log",
)
FEATURE_NAMES = (
    BASE_FEATURE_NAMES
    + tuple(f"league_bucket_{i}" for i in range(LEAGUE_HASH_BUCKETS))
    + tuple(f"market_bucket_{i}" for i in range(MARKET_HASH_BUCKETS))
    + tuple(f"market_bucket_movement_{i}" for i in range(MARKET_HASH_BUCKETS))
)


def compile_features(
    latest: Mapping[str, Any],
    *,
    earliest: Mapping[str, Any] | None,
    hours_to_start: float,
) -> dict[str, Any]:
    current = consensus_three_way(latest)
    if not current:
        raise ValueError("complete three-way h2h market is required")
    previous = consensus_three_way(earliest) if earliest else None
    prior = current["probabilities"]
    movement = {
        side: prior[side] - (previous["probabilities"][side] if previous else prior[side])
        for side in OUTCOMES
    }
    total_points = _market_points(latest, "totals")
    spread_points = _market_points(latest, "spreads")
    all_markets = _all_market_summary(latest)
    previous_all_markets = _all_market_summary(earliest) if earliest else all_markets
    values = [
        prior["home"], prior["draw"], prior["away"],
        current["dispersion"]["home"], current["dispersion"]["draw"], current["dispersion"]["away"],
        movement["home"], movement["draw"], movement["away"],
        math.log1p(current["book_count"]),
        math.log1p(max(0.0, hours_to_start)),
        statistics.median(total_points) if total_points else 0.0,
        statistics.median([abs(x) for x in spread_points]) if spread_points else 0.0,
        math.log1p(all_markets["market_count"]),
        math.log1p(all_markets["pairs"]),
        math.log1p(all_markets["outcomes"]),
        all_markets["price_mean"],
        all_markets["price_std"],
        all_markets["point_abs_mean"],
        all_markets["point_std"],
        math.log1p(all_markets["player_pairs"]),
        math.log1p(all_markets["corner_pairs"]),
        math.log1p(all_markets["card_pairs"]),
        math.log1p(all_markets["period_pairs"]),
    ]
    buckets = [0.0] * LEAGUE_HASH_BUCKETS
    buckets[_league_bucket(str(latest.get("sport_key") or "soccer_unknown"))] = 1.0
    values.extend(buckets)
    values.extend(math.log1p(value) for value in all_markets["buckets"])
    values.extend(
        math.copysign(math.log1p(abs(current - previous)), current - previous)
        for current, previous in zip(all_markets["buckets"], previous_all_markets["buckets"])
    )
    return {
        "schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "values": values,
        "market_prior": [prior[side] for side in OUTCOMES],
        "book_count": current["book_count"],
        "source_bookmakers": sorted(current["by_book"]),
        "all_market_count": all_markets["market_count"],
        "all_book_market_pair_count": all_markets["pairs"],
        "all_outcome_count": all_markets["outcomes"],
    }
