"""Cost-guarded live MLB V8 Odds API shadow collector."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Mapping

import boto3

import mlb_odds_market_expansion_v8 as v8

VERSION = "MLB-ODDS-MARKET-V8-COLLECTOR-v1"
API_KEY = os.environ.get("ODDS_API_KEY", "")
TABLE_NAME = os.environ.get("MLB_V8_FEATURE_TABLE", "")
TTL_SECONDS = max(86400, int(os.environ.get("MLB_V8_TTL_SECONDS", "7776000")))
HTTP_TIMEOUT = max(5, int(os.environ.get("MLB_V8_HTTP_TIMEOUT_SECONDS", "20")))
TABLE = boto3.resource("dynamodb").Table(TABLE_NAME) if TABLE_NAME else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(x) for x in value]
    return value


def _ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(k): _ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_ddb(x) for x in value]
    return value


def _headers(raw: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key in ("x-requests-used", "x-requests-remaining", "x-requests-last"):
        value = raw.get(key)
        if value is not None:
            try: result[key] = int(value)
            except Exception: result[key] = str(value)
    return result


def _get(url: str) -> tuple[Any, Dict[str, Any]]:
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": VERSION})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8")), _headers(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                raise RuntimeError(f"Odds API HTTP {exc.code}: {body}") from exc
            if attempt == 4:
                raise RuntimeError(f"Odds API retryable HTTP {exc.code} exhausted") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 4:
                raise RuntimeError("Odds API network retries exhausted") from exc
        time.sleep(min(16, 2 ** attempt))
    raise RuntimeError("unreachable retry state")


def _available_market_keys(payload: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, Mapping):
        books = payload.get("bookmakers") or []
    else:
        books = []
    for book in books:
        if not isinstance(book, Mapping): continue
        for market in book.get("markets") or []:
            if isinstance(market, Mapping) and market.get("key"):
                keys.add(str(market["key"]))
    return keys


def _write(event_id: str, collected_at: str, payload: Mapping[str, Any]) -> None:
    if TABLE is None:
        raise RuntimeError("MLB_V8_FEATURE_TABLE is not configured")
    expires = int(_now().timestamp()) + TTL_SECONDS
    TABLE.put_item(Item=_ddb({
        "PK": f"MLB_V8_EVENT#{event_id}",
        "SK": f"SNAPSHOT#{collected_at}",
        "record_type": "mlb_v8_odds_market_shadow_snapshot",
        "event_id": event_id,
        "collected_at": collected_at,
        "expires_at": expires,
        "data": payload,
    }))
    TABLE.put_item(Item=_ddb({
        "PK": f"MLB_V8_EVENT#{event_id}",
        "SK": "LATEST",
        "record_type": "mlb_v8_odds_market_shadow_latest",
        "event_id": event_id,
        "collected_at": collected_at,
        "expires_at": expires,
        "data": payload,
    }))


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    cfg = v8.load_config()
    if not cfg.enabled:
        return {"ok": True, "status": "DISABLED", "version": VERSION, "contract": v8.shadow_contract(cfg)}
    if not API_KEY or TABLE is None:
        raise RuntimeError("V8 collector configuration is incomplete")

    collected_at = _now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
    featured, featured_headers = _get(v8.featured_odds_url(API_KEY, config=cfg))
    featured_rows = [x for x in featured if isinstance(x, Mapping)] if isinstance(featured, list) else []
    events_by_id = {str(x.get("id")): x for x in featured_rows if x.get("id")}

    planned_events = list(events_by_id)[:cfg.max_events_per_cycle]
    discovered: Dict[str, list[str]] = {}
    selected_by_event: Dict[str, tuple[str, ...]] = {}
    for event_id in planned_events:
        market_payload, _ = _get(v8.event_markets_url(API_KEY, event_id, cfg))
        keys = _available_market_keys(market_payload)
        discovered[event_id] = sorted(keys)
        selected_by_event[event_id] = v8.selected_event_markets(keys, cfg)

    maximum_selected = max((len(x) for x in selected_by_event.values()), default=0)
    budget = v8.enforce_cycle_budget(
        event_count=len(planned_events), event_market_count=maximum_selected, config=cfg,
    )
    if not budget["withinBudget"]:
        return {
            "ok": False, "status": "PAUSED_COST_GUARD", "version": VERSION,
            "budget": budget, "eventCount": len(planned_events), "contract": v8.shadow_contract(cfg),
        }

    written = 0
    total_last_cost = int(featured_headers.get("x-requests-last") or 0)
    for event_id, raw_featured in events_by_id.items():
        if event_id not in planned_events:
            continue
        selected = selected_by_event.get(event_id) or ()
        combined = dict(raw_featured)
        event_headers: Dict[str, Any] = {}
        if selected:
            event_payload, event_headers = _get(v8.event_odds_url(API_KEY, event_id, selected, config=cfg))
            if isinstance(event_payload, Mapping):
                base_books = list(combined.get("bookmakers") or [])
                base_by_key = {str(x.get("key") or x.get("title") or ""): dict(x) for x in base_books if isinstance(x, Mapping)}
                for book in event_payload.get("bookmakers") or []:
                    if not isinstance(book, Mapping): continue
                    key = str(book.get("key") or book.get("title") or "")
                    existing = base_by_key.setdefault(key, dict(book))
                    markets = list(existing.get("markets") or [])
                    seen = {str(x.get("key") or "") for x in markets if isinstance(x, Mapping)}
                    markets.extend(dict(x) for x in book.get("markets") or [] if isinstance(x, Mapping) and str(x.get("key") or "") not in seen)
                    existing["markets"] = markets
                combined["bookmakers"] = list(base_by_key.values())
        normalized = v8.normalize_event(combined)
        features = v8.derive_team_level_features(normalized)
        payload = {
            "version": VERSION,
            "authority": "SHADOW_ONLY",
            "productionV7Unchanged": True,
            "collectedAtUtc": collected_at,
            "event": normalized,
            "features": features,
            "discoveredMarkets": discovered.get(event_id, []),
            "selectedEventMarkets": list(selected),
            "featuredResponseHeaders": featured_headers,
            "eventResponseHeaders": event_headers,
            "budget": budget,
        }
        _write(event_id, collected_at, payload)
        total_last_cost += int(event_headers.get("x-requests-last") or 0)
        written += 1

    return {
        "ok": True,
        "status": "COLLECTED_SHADOW",
        "version": VERSION,
        "collectedAtUtc": collected_at,
        "eventCount": len(events_by_id),
        "writtenEventCount": written,
        "estimatedBudget": budget,
        "reportedLastRequestCostSum": total_last_cost,
        "contract": v8.shadow_contract(cfg),
    }
