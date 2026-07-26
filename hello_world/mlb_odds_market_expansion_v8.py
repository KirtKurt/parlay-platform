"""Versioned MLB Odds API market expansion for shadow-only optimizer testing.

V8 adds higher-value Odds API markets without changing the active V7 prediction
authority. It supports featured spreads/totals, event-market discovery, first-five
markets, selected regional comparison, alternate lines, and tightly allowlisted
player-prop aggregation. Every capability is feature-flagged and cost-guarded.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-ODDS-MARKET-EXPANSION-v8-shadow"
SPORT_KEY = "baseball_mlb"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

FEATURED_MARKETS = ("h2h", "spreads", "totals")
FIRST_FIVE_MARKETS = (
    "h2h_1st_5_innings",
    "spreads_1st_5_innings",
    "totals_1st_5_innings",
)
ALTERNATE_MARKETS = ("alternate_spreads", "alternate_totals")
TEAM_PROP_MARKETS = ("team_totals",)
PLAYER_PROP_ALLOWLIST = (
    "batter_hits",
    "batter_total_bases",
    "pitcher_strikeouts",
    "pitcher_hits_allowed",
    "pitcher_earned_runs",
)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: Sequence[str]) -> Tuple[str, ...]:
    raw = os.environ.get(name)
    values = list(default) if raw is None else [x.strip() for x in raw.split(",")]
    return tuple(dict.fromkeys(x for x in values if x))


@dataclass(frozen=True)
class V8Config:
    enabled: bool
    regions: Tuple[str, ...]
    featured_markets: Tuple[str, ...]
    first_five_enabled: bool
    alternates_enabled: bool
    team_props_enabled: bool
    player_props_enabled: bool
    max_event_markets: int
    max_events_per_cycle: int
    max_estimated_credits_per_cycle: int


def load_config() -> V8Config:
    regions = _csv("MLB_V8_REGIONS", ("us", "us2"))
    if len(regions) > 3:
        regions = regions[:3]
    featured = tuple(x for x in _csv("MLB_V8_FEATURED_MARKETS", FEATURED_MARKETS) if x in FEATURED_MARKETS)
    return V8Config(
        enabled=_truthy("MLB_V8_ENABLED", False),
        regions=regions or ("us",),
        featured_markets=featured or FEATURED_MARKETS,
        first_five_enabled=_truthy("MLB_V8_FIRST_FIVE_ENABLED", True),
        alternates_enabled=_truthy("MLB_V8_ALTERNATES_ENABLED", True),
        team_props_enabled=_truthy("MLB_V8_TEAM_PROPS_ENABLED", True),
        player_props_enabled=_truthy("MLB_V8_PLAYER_PROPS_ENABLED", False),
        max_event_markets=max(1, min(40, int(os.environ.get("MLB_V8_MAX_EVENT_MARKETS", "18")))),
        max_events_per_cycle=max(1, min(20, int(os.environ.get("MLB_V8_MAX_EVENTS_PER_CYCLE", "8")))),
        max_estimated_credits_per_cycle=max(1, int(os.environ.get("MLB_V8_MAX_CREDITS_PER_CYCLE", "500"))),
    )


def _query(params: Mapping[str, Any]) -> str:
    return urllib.parse.urlencode([(k, v) for k, v in params.items() if v not in (None, "")])


def featured_odds_url(api_key: str, *, historical_at: str | None = None, config: V8Config | None = None) -> str:
    cfg = config or load_config()
    params = {
        "apiKey": api_key,
        "regions": ",".join(cfg.regions),
        "markets": ",".join(cfg.featured_markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if historical_at:
        params["date"] = historical_at
        return f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY}/odds?{_query(params)}"
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds?{_query(params)}"


def event_markets_url(api_key: str, event_id: str) -> str:
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/markets?{_query({'apiKey': api_key})}"


def selected_event_markets(available: Iterable[str], config: V8Config | None = None) -> Tuple[str, ...]:
    cfg = config or load_config()
    allowed: List[str] = []
    preferred: List[str] = []
    if cfg.first_five_enabled:
        preferred.extend(FIRST_FIVE_MARKETS)
    if cfg.alternates_enabled:
        preferred.extend(ALTERNATE_MARKETS)
    if cfg.team_props_enabled:
        preferred.extend(TEAM_PROP_MARKETS)
    if cfg.player_props_enabled:
        preferred.extend(PLAYER_PROP_ALLOWLIST)
    available_set = {str(x) for x in available}
    for market in preferred:
        if market in available_set and market not in allowed:
            allowed.append(market)
        if len(allowed) >= cfg.max_event_markets:
            break
    return tuple(allowed)


def event_odds_url(
    api_key: str,
    event_id: str,
    markets: Sequence[str],
    *,
    historical_at: str | None = None,
    config: V8Config | None = None,
) -> str:
    cfg = config or load_config()
    params = {
        "apiKey": api_key,
        "regions": ",".join(cfg.regions),
        "markets": ",".join(markets),
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    if historical_at:
        params["date"] = historical_at
        return f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY}/events/{event_id}/odds?{_query(params)}"
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds?{_query(params)}"


def estimate_featured_credits(config: V8Config | None = None) -> int:
    cfg = config or load_config()
    return len(cfg.regions) * len(cfg.featured_markets)


def estimate_event_credits(event_count: int, market_count: int, config: V8Config | None = None) -> int:
    cfg = config or load_config()
    return max(0, event_count) * max(0, market_count) * len(cfg.regions)


def enforce_cycle_budget(*, event_count: int, event_market_count: int, config: V8Config | None = None) -> Dict[str, Any]:
    cfg = config or load_config()
    featured = estimate_featured_credits(cfg)
    event_cost = estimate_event_credits(min(event_count, cfg.max_events_per_cycle), event_market_count, cfg)
    total = featured + event_cost
    return {
        "version": VERSION,
        "featuredEstimatedCredits": featured,
        "eventEstimatedCredits": event_cost,
        "estimatedCredits": total,
        "maximumCredits": cfg.max_estimated_credits_per_cycle,
        "withinBudget": total <= cfg.max_estimated_credits_per_cycle,
    }


def _american_to_probability(price: Any) -> float | None:
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    return 100.0 / (value + 100.0) if value > 0 else (-value) / ((-value) + 100.0)


def _outcomes(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows = market.get("outcomes")
    return [x for x in rows if isinstance(x, Mapping)] if isinstance(rows, list) else []


def normalize_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    books = event.get("bookmakers") if isinstance(event.get("bookmakers"), list) else []
    normalized_books: List[Dict[str, Any]] = []
    for book in books:
        if not isinstance(book, Mapping):
            continue
        markets: Dict[str, List[Dict[str, Any]]] = {}
        for market in book.get("markets") or []:
            if not isinstance(market, Mapping):
                continue
            key = str(market.get("key") or "")
            if not key:
                continue
            rows: List[Dict[str, Any]] = []
            for outcome in _outcomes(market):
                rows.append({
                    "name": outcome.get("name"),
                    "description": outcome.get("description"),
                    "price": outcome.get("price"),
                    "point": outcome.get("point"),
                    "impliedProbability": _american_to_probability(outcome.get("price")),
                })
            markets[key] = rows
        normalized_books.append({
            "key": book.get("key"),
            "title": book.get("title"),
            "lastUpdate": book.get("last_update"),
            "markets": markets,
        })
    value = {
        "version": VERSION,
        "eventId": event.get("id"),
        "sportKey": event.get("sport_key"),
        "commenceTime": event.get("commence_time"),
        "homeTeam": event.get("home_team"),
        "awayTeam": event.get("away_team"),
        "bookmakers": normalized_books,
    }
    value["fingerprint"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def _market_rows(event: Mapping[str, Any], market_key: str) -> List[Mapping[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping):
            continue
        market = (book.get("markets") or {}).get(market_key)
        if isinstance(market, list):
            rows.extend(x for x in market if isinstance(x, Mapping))
    return rows


def _median(values: Sequence[float]) -> float | None:
    data = sorted(values)
    if not data:
        return None
    n = len(data)
    return data[n // 2] if n % 2 else (data[n // 2 - 1] + data[n // 2]) / 2.0


def derive_team_level_features(event: Mapping[str, Any]) -> Dict[str, Any]:
    """Produce stable team-level features only; raw props never become direct picks."""
    out: Dict[str, Any] = {"version": VERSION}
    for key in FEATURED_MARKETS + FIRST_FIVE_MARKETS:
        rows = _market_rows(event, key)
        probs = [float(x["impliedProbability"]) for x in rows if x.get("impliedProbability") is not None]
        points = [float(x["point"]) for x in rows if x.get("point") is not None]
        out[f"{key}BookOutcomeCount"] = len(rows)
        out[f"{key}MedianImpliedProbability"] = _median(probs)
        out[f"{key}MedianPoint"] = _median(points)
        if probs:
            out[f"{key}ProbabilityDispersion"] = max(probs) - min(probs)
    full_total = out.get("totalsMedianPoint")
    f5_total = out.get("totals_1st_5_inningsMedianPoint")
    if isinstance(full_total, (int, float)) and isinstance(f5_total, (int, float)):
        out["impliedLateInningRunEnvironment"] = float(full_total) - float(f5_total)
    full_spread = out.get("spreadsMedianPoint")
    f5_spread = out.get("spreads_1st_5_inningsMedianPoint")
    if isinstance(full_spread, (int, float)) and isinstance(f5_spread, (int, float)):
        out["starterBullpenSpreadDivergence"] = float(full_spread) - float(f5_spread)
    prop_values: List[float] = []
    for key in PLAYER_PROP_ALLOWLIST:
        for row in _market_rows(event, key):
            if row.get("point") is not None:
                prop_values.append(float(row["point"]))
    out["allowlistedPlayerPropObservationCount"] = len(prop_values)
    out["allowlistedPlayerPropMedianLine"] = _median(prop_values)
    return out


def shadow_contract(config: V8Config | None = None) -> Dict[str, Any]:
    cfg = config or load_config()
    return {
        "version": VERSION,
        "enabled": cfg.enabled,
        "authority": "SHADOW_ONLY",
        "productionV7Unchanged": True,
        "regions": list(cfg.regions),
        "featuredMarkets": list(cfg.featured_markets),
        "firstFiveEnabled": cfg.first_five_enabled,
        "alternatesEnabled": cfg.alternates_enabled,
        "teamPropsEnabled": cfg.team_props_enabled,
        "playerPropsEnabled": cfg.player_props_enabled,
        "promotionRequiresUntouchedAudit80Pct": True,
    }
