"""Versioned MLB Odds API market expansion for shadow optimizer testing.

V8 adds featured spreads/totals plus discovery-gated first-five, regional,
alternate and selected prop observations. Production authority remains V7 until
chronological walk-forward and untouched-audit gates pass.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-ODDS-MARKET-EXPANSION-v8.2-shadow-corrected"
SPORT_KEY = "baseball_mlb"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
FEATURED_MARKETS = ("h2h", "spreads", "totals")
FIRST_FIVE_MARKETS = ("h2h_1st_5_innings", "spreads_1st_5_innings", "totals_1st_5_innings")
ALTERNATE_MARKETS = ("alternate_spreads", "alternate_totals")
TEAM_PROP_MARKETS = ("team_totals",)
PLAYER_PROP_ALLOWLIST = (
    "batter_hits", "batter_total_bases", "pitcher_strikeouts",
    "pitcher_hits_allowed", "pitcher_earned_runs",
)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: Sequence[str]) -> Tuple[str, ...]:
    raw = os.environ.get(name)
    values = list(default) if raw is None else [x.strip() for x in raw.split(",")]
    return tuple(dict.fromkeys(x for x in values if x))


def _bounded_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class V8Config:
    enabled: bool
    featured_regions: Tuple[str, ...]
    event_regions: Tuple[str, ...]
    featured_markets: Tuple[str, ...]
    first_five_enabled: bool
    alternates_enabled: bool
    team_props_enabled: bool
    player_props_enabled: bool
    max_event_markets: int
    max_events_per_cycle: int
    max_estimated_credits_per_cycle: int


def load_config() -> V8Config:
    valid_regions = {"us", "us2", "uk", "eu", "au"}
    featured_regions = tuple(x for x in _csv("MLB_V8_FEATURED_REGIONS", ("us",)) if x in valid_regions)[:2] or ("us",)
    event_regions = tuple(x for x in _csv("MLB_V8_EVENT_REGIONS", ("us", "us2")) if x in valid_regions)[:3] or ("us",)
    featured = tuple(x for x in _csv("MLB_V8_FEATURED_MARKETS", FEATURED_MARKETS) if x in FEATURED_MARKETS)
    return V8Config(
        enabled=_truthy("MLB_V8_ENABLED", False),
        featured_regions=featured_regions,
        event_regions=event_regions,
        featured_markets=featured or FEATURED_MARKETS,
        first_five_enabled=_truthy("MLB_V8_FIRST_FIVE_ENABLED", True),
        alternates_enabled=_truthy("MLB_V8_ALTERNATES_ENABLED", True),
        team_props_enabled=_truthy("MLB_V8_TEAM_PROPS_ENABLED", True),
        player_props_enabled=_truthy("MLB_V8_PLAYER_PROPS_ENABLED", False),
        max_event_markets=_bounded_int("MLB_V8_MAX_EVENT_MARKETS", 18, 1, 40),
        max_events_per_cycle=_bounded_int("MLB_V8_MAX_EVENTS_PER_CYCLE", 8, 1, 20),
        max_estimated_credits_per_cycle=_bounded_int("MLB_V8_MAX_CREDITS_PER_CYCLE", 500, 1, 100000),
    )


def _query(params: Mapping[str, Any]) -> str:
    return urllib.parse.urlencode([(k, v) for k, v in params.items() if v not in (None, "")])


def featured_odds_url(api_key: str, *, historical_at: str | None = None, config: V8Config | None = None) -> str:
    cfg = config or load_config()
    params = {
        "apiKey": api_key, "regions": ",".join(cfg.featured_regions),
        "markets": ",".join(cfg.featured_markets), "oddsFormat": "american",
        "dateFormat": "iso", "includeSids": "true",
    }
    if historical_at:
        params["date"] = historical_at
        return f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY}/odds?{_query(params)}"
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds?{_query(params)}"


def events_url(api_key: str, *, historical_at: str | None = None) -> str:
    params = {"apiKey": api_key, "dateFormat": "iso"}
    if historical_at:
        params["date"] = historical_at
        return f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY}/events?{_query(params)}"
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events?{_query(params)}"


def event_markets_url(api_key: str, event_id: str, config: V8Config | None = None) -> str:
    cfg = config or load_config()
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/markets?{_query({'apiKey': api_key, 'regions': ','.join(cfg.event_regions), 'dateFormat': 'iso'})}"


def selected_event_markets(available: Iterable[str], config: V8Config | None = None) -> Tuple[str, ...]:
    cfg = config or load_config()
    preferred: List[str] = []
    if cfg.first_five_enabled: preferred.extend(FIRST_FIVE_MARKETS)
    if cfg.alternates_enabled: preferred.extend(ALTERNATE_MARKETS)
    if cfg.team_props_enabled: preferred.extend(TEAM_PROP_MARKETS)
    if cfg.player_props_enabled: preferred.extend(PLAYER_PROP_ALLOWLIST)
    available_set = {str(x) for x in available}
    return tuple(dict.fromkeys(x for x in preferred if x in available_set))[:cfg.max_event_markets]


def event_odds_url(api_key: str, event_id: str, markets: Sequence[str], *, historical_at: str | None = None, config: V8Config | None = None) -> str:
    cfg = config or load_config()
    params = {
        "apiKey": api_key, "regions": ",".join(cfg.event_regions),
        "markets": ",".join(markets), "oddsFormat": "american",
        "dateFormat": "iso", "includeSids": "true",
    }
    if historical_at:
        params["date"] = historical_at
        return f"{ODDS_API_BASE}/historical/sports/{SPORT_KEY}/events/{event_id}/odds?{_query(params)}"
    return f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds?{_query(params)}"


def estimate_featured_credits(config: V8Config | None = None, *, historical: bool = False) -> int:
    cfg = config or load_config()
    return (10 if historical else 1) * len(cfg.featured_regions) * len(cfg.featured_markets)


def estimate_discovery_credits(event_count: int, config: V8Config | None = None, *, historical: bool = False) -> int:
    if historical:
        return 0
    cfg = config or load_config()
    return max(0, min(event_count, cfg.max_events_per_cycle))


def estimate_event_credits(event_count: int, market_count: int, config: V8Config | None = None, *, historical: bool = False) -> int:
    cfg = config or load_config()
    return (10 if historical else 1) * max(0, event_count) * max(0, market_count) * len(cfg.event_regions)


def enforce_cycle_budget(*, event_count: int, event_market_count: int, config: V8Config | None = None, historical: bool = False) -> Dict[str, Any]:
    cfg = config or load_config()
    bounded_events = min(max(0, event_count), cfg.max_events_per_cycle)
    featured = estimate_featured_credits(cfg, historical=historical)
    discovery = estimate_discovery_credits(bounded_events, cfg, historical=historical)
    event_cost = estimate_event_credits(bounded_events, event_market_count, cfg, historical=historical)
    total = featured + discovery + event_cost
    return {
        "version": VERSION, "featuredEstimatedCredits": featured,
        "discoveryEstimatedCredits": discovery, "eventEstimatedCredits": event_cost,
        "estimatedCredits": total, "maximumCredits": cfg.max_estimated_credits_per_cycle,
        "withinBudget": total <= cfg.max_estimated_credits_per_cycle,
    }


def _american_to_probability(price: Any) -> float | None:
    try: value = float(price)
    except (TypeError, ValueError): return None
    if value == 0: return None
    return 100.0 / (value + 100.0) if value > 0 else (-value) / ((-value) + 100.0)


def _outcomes(market: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    rows = market.get("outcomes")
    return [x for x in rows if isinstance(x, Mapping)] if isinstance(rows, list) else []


def normalize_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    normalized_books: List[Dict[str, Any]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping): continue
        markets: Dict[str, List[Dict[str, Any]]] = {}
        for market in book.get("markets") or []:
            if not isinstance(market, Mapping): continue
            key = str(market.get("key") or "")
            if not key: continue
            markets[key] = [{
                "name": outcome.get("name"), "description": outcome.get("description"),
                "price": outcome.get("price"), "point": outcome.get("point"),
                "sid": outcome.get("sid"), "impliedProbability": _american_to_probability(outcome.get("price")),
            } for outcome in _outcomes(market)]
        normalized_books.append({
            "key": book.get("key"), "title": book.get("title"), "sid": book.get("sid"),
            "lastUpdate": book.get("last_update"), "markets": markets,
        })
    value = {
        "version": VERSION, "eventId": event.get("id"), "sportKey": event.get("sport_key"),
        "commenceTime": event.get("commence_time"), "homeTeam": event.get("home_team"),
        "awayTeam": event.get("away_team"), "bookmakers": normalized_books,
    }
    value["fingerprint"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    return value


def _market_rows(event: Mapping[str, Any], market_key: str) -> List[Mapping[str, Any]]:
    rows: List[Mapping[str, Any]] = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, Mapping): continue
        market = (book.get("markets") or {}).get(market_key)
        if isinstance(market, list): rows.extend(x for x in market if isinstance(x, Mapping))
    return rows


def _median(values: Sequence[float]) -> float | None:
    data = sorted(values)
    if not data: return None
    n = len(data)
    return data[n // 2] if n % 2 else (data[n // 2 - 1] + data[n // 2]) / 2.0


def _side_rows(event: Mapping[str, Any], market_key: str, side: str) -> List[Mapping[str, Any]]:
    side_l = side.strip().lower()
    return [row for row in _market_rows(event, market_key) if str(row.get("name") or "").strip().lower() == side_l]


def _market_side_features(event: Mapping[str, Any], market_key: str, side: str) -> Dict[str, Any]:
    rows = _side_rows(event, market_key, side)
    probs = [float(x["impliedProbability"]) for x in rows if x.get("impliedProbability") is not None]
    points = [float(x["point"]) for x in rows if x.get("point") is not None]
    prefix = f"{market_key}_{side.replace(' ', '_')}"
    return {
        f"{prefix}BookCount": len(rows), f"{prefix}MedianImpliedProbability": _median(probs),
        f"{prefix}MedianPoint": _median(points),
        f"{prefix}ProbabilityDispersion": max(probs) - min(probs) if probs else None,
    }


def derive_team_level_features(event: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"version": VERSION}
    home = str(event.get("homeTeam") or "")
    away = str(event.get("awayTeam") or "")
    for key in ("h2h", "spreads", "h2h_1st_5_innings", "spreads_1st_5_innings"):
        if home: out.update(_market_side_features(event, key, home))
        if away: out.update(_market_side_features(event, key, away))
    for key in ("totals", "totals_1st_5_innings", "alternate_totals"):
        out.update(_market_side_features(event, key, "Over"))
        out.update(_market_side_features(event, key, "Under"))
    for key in ("alternate_spreads", "team_totals"):
        rows = _market_rows(event, key)
        out[f"{key}ObservationCount"] = len(rows)
    full_total = out.get("totals_OverMedianPoint")
    f5_total = out.get("totals_1st_5_innings_OverMedianPoint")
    if isinstance(full_total, (int, float)) and isinstance(f5_total, (int, float)):
        out["impliedLateInningRunEnvironment"] = float(full_total) - float(f5_total)
    if home:
        full_spread = out.get(f"spreads_{home.replace(' ', '_')}MedianPoint")
        f5_spread = out.get(f"spreads_1st_5_innings_{home.replace(' ', '_')}MedianPoint")
        if isinstance(full_spread, (int, float)) and isinstance(f5_spread, (int, float)):
            out["homeStarterBullpenSpreadDivergence"] = float(full_spread) - float(f5_spread)
    # Player markets remain observational until an authoritative player-to-team map is supplied.
    prop_rows = [row for key in PLAYER_PROP_ALLOWLIST for row in _market_rows(event, key)]
    out["allowlistedPlayerPropObservationCount"] = len(prop_rows)
    out["playerPropsTeamAttributionAvailable"] = False
    out["playerPropsEligibleForTraining"] = False
    return out


def _event_feature_by_id(payload: Any) -> Dict[str, Dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, Mapping) else payload
    result: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if isinstance(row, Mapping) and row.get("id"):
            normalized = normalize_event(row)
            result[str(row["id"])] = derive_team_level_features(normalized)
    return result


def install(optimizer: Any, policy_runtime: Any) -> None:
    """Patch snapshot normalization only; V8 features remain shadow metadata."""
    if getattr(optimizer, "_INQSI_ODDS_MARKET_V8_INSTALLED", False): return
    original = optimizer.normalize_historical_snapshot
    def patched(payload: Any, requested_at: Any) -> Dict[str, Any]:
        out = original(payload, requested_at)
        by_id = _event_feature_by_id(payload)
        for event in out.get("events") or []:
            features = by_id.get(str(event.get("providerEventId") or ""))
            if features:
                event["oddsMarketExpansionFeatures"] = copy.deepcopy(features)
                event["oddsMarketExpansionVersion"] = VERSION
        out["oddsMarketExpansionVersion"] = VERSION
        out["oddsMarketExpansionShadowOnly"] = True
        return out
    optimizer.normalize_historical_snapshot = patched
    optimizer.ODDS_MARKET_EXPANSION_VERSION = VERSION
    optimizer._INQSI_ODDS_MARKET_V8_INSTALLED = True


def shadow_contract(config: V8Config | None = None) -> Dict[str, Any]:
    cfg = config or load_config()
    return {
        "version": VERSION, "enabled": cfg.enabled, "authority": "SHADOW_ONLY",
        "productionV7Unchanged": True, "featuredRegions": list(cfg.featured_regions),
        "eventRegions": list(cfg.event_regions), "featuredMarkets": list(cfg.featured_markets),
        "firstFiveEnabled": cfg.first_five_enabled, "alternatesEnabled": cfg.alternates_enabled,
        "teamPropsEnabled": cfg.team_props_enabled, "playerPropsEnabled": cfg.player_props_enabled,
        "playerPropsRequireAuthoritativeTeamAttribution": True,
        "promotionRequiresUntouchedAudit80Pct": True,
    }
