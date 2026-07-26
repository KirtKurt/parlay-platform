"""Isolated V8 Odds API shadow collector.

This module never changes V7 authority. It collects featured and selected event-level
markets, archives content-addressed evidence, and returns bounded shadow features.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import boto3
from botocore.exceptions import ClientError

import mlb_odds_market_expansion_v8 as v8

VERSION = "MLB-ODDS-V8-SHADOW-COLLECTOR-v1.3"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BUCKET = os.environ.get("MLB_V8_SHADOW_BUCKET", "")


def _safe_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


HTTP_TIMEOUT = _safe_int("MLB_V8_HTTP_TIMEOUT_SECONDS", 20, 5, 120)
_S3 = boto3.client("s3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_historical_at(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text.endswith("Z"):
        raise ValueError("historicalAtUtc must be UTC ISO8601 ending in Z")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("historicalAtUtc is invalid ISO8601") from exc
    return text


def _get(url: str) -> Tuple[Any, Dict[str, str]]:
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": VERSION})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                body = json.loads(response.read().decode("utf-8"))
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                return body, headers
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 4:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
        time.sleep(min(16, 2**attempt))
    raise RuntimeError("V8 shadow HTTP retry state exhausted")


def _put_immutable(prefix: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    if not BUCKET:
        raise RuntimeError("MLB_V8_SHADOW_BUCKET is not configured")
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    key = f"{prefix.rstrip('/')}/{digest}.json"
    try:
        response = _S3.put_object(
            Bucket=BUCKET, Key=key, Body=body, ContentType="application/json",
            ServerSideEncryption="AES256", IfNoneMatch="*",
            Metadata={"record-type": "mlb_odds_v8_shadow", "version": VERSION, "sha256": digest},
        )
        etag = str(response.get("ETag") or "").strip('"')
        created = True
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"PreconditionFailed", "412"}:
            raise
        etag = ""
        created = False
    return {"bucket": BUCKET, "key": key, "etag": etag, "sha256": digest, "created": created}


def _events_from_featured(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
        payload = payload["data"]
    return [x for x in payload if isinstance(x, Mapping)] if isinstance(payload, list) else []


def _available_market_keys(payload: Any) -> List[str]:
    keys: List[str] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = list(payload.get("markets") or payload.get("data") or [])
        for book in payload.get("bookmakers") or []:
            if isinstance(book, Mapping):
                rows.extend(book.get("markets") or [])
    else:
        rows = []
    for row in rows:
        if isinstance(row, str):
            keys.append(row)
        elif isinstance(row, Mapping) and row.get("key"):
            keys.append(str(row["key"]))
    return sorted(set(x for x in keys if x))


def _historical_market_plan(cfg: v8.V8Config) -> Tuple[str, ...]:
    preferred: List[str] = []
    if cfg.first_five_enabled: preferred.extend(v8.FIRST_FIVE_MARKETS)
    if cfg.alternates_enabled: preferred.extend(v8.ALTERNATE_MARKETS)
    if cfg.team_props_enabled: preferred.extend(v8.TEAM_PROP_MARKETS)
    if cfg.player_props_enabled: preferred.extend(v8.PLAYER_PROP_ALLOWLIST)
    return tuple(preferred[: cfg.max_event_markets])


def _plan_budget_before_discovery(event_count: int, cfg: v8.V8Config, historical: bool) -> Dict[str, Any]:
    # Use maximum configured event-market count before spending discovery credits.
    return v8.enforce_cycle_budget(
        event_count=min(event_count, cfg.max_events_per_cycle),
        event_market_count=cfg.max_event_markets,
        config=cfg,
        historical=historical,
    )


def _fetch_event_markets_individually(
    event_id: str,
    markets: Sequence[str],
    historical_at: str | None,
    cfg: v8.V8Config,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    enriched: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    # One-market requests isolate unsupported historical markets and preserve usable data.
    for market in markets:
        try:
            raw, headers = _get(v8.event_odds_url(API_KEY, event_id, (market,), historical_at=historical_at, config=cfg))
        except urllib.error.HTTPError as exc:
            errors.append({"eventId": event_id, "status": exc.code, "markets": [market]})
            continue
        payload = raw.get("data") if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else raw
        if not isinstance(payload, Mapping):
            errors.append({"eventId": event_id, "status": "MALFORMED_PAYLOAD", "markets": [market]})
            continue
        normalized_event = v8.normalize_event(payload)
        enriched.append({
            "event": normalized_event,
            "features": v8.derive_team_level_features(normalized_event),
            "selectedMarkets": [market],
            "headers": headers,
        })
    return enriched, errors


def collect_once(*, historical_at: str | None = None) -> Dict[str, Any]:
    historical_at = _validate_historical_at(historical_at)
    cfg = v8.load_config()
    if not cfg.enabled:
        return {"ok": True, "status": "DISABLED", "version": VERSION, "contract": v8.shadow_contract(cfg)}
    if not API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")

    is_historical = historical_at is not None
    featured_raw, featured_headers = _get(v8.featured_odds_url(API_KEY, historical_at=historical_at, config=cfg))
    raw_events = _events_from_featured(featured_raw)
    selected_events = raw_events[: cfg.max_events_per_cycle]

    pre_budget = _plan_budget_before_discovery(len(selected_events), cfg, is_historical)
    if not pre_budget["withinBudget"]:
        return {
            "ok": False, "status": "BLOCKED_COST_GUARD_PRE_DISCOVERY", "version": VERSION,
            "budget": pre_budget, "eventCount": len(raw_events), "productionAuthorityChanged": False,
        }

    discoveries: List[Dict[str, Any]] = []
    plans: List[Tuple[Mapping[str, Any], Tuple[str, ...]]] = []
    total_selected_markets = 0
    for event in selected_events:
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        if is_historical:
            selected = _historical_market_plan(cfg)
            discoveries.append({
                "eventId": event_id, "availableMarkets": [], "selectedMarkets": list(selected),
                "discoveryMode": "HISTORICAL_ALLOWLIST_NO_DISCOVERY_ENDPOINT",
            })
        else:
            available_raw, discovery_headers = _get(v8.event_markets_url(API_KEY, event_id, cfg))
            available = _available_market_keys(available_raw)
            selected = v8.selected_event_markets(available, cfg)
            discoveries.append({
                "eventId": event_id, "availableMarkets": available, "selectedMarkets": list(selected),
                "headers": discovery_headers, "discoveryMode": "LIVE_EVENT_MARKETS",
            })
        if selected:
            plans.append((event, selected))
            total_selected_markets += len(selected)

    # Exact upper bound for one-market requests across all selected events and regions.
    event_count_for_cost = len(plans)
    max_markets_for_any_event = max((len(markets) for _, markets in plans), default=0)
    budget = v8.enforce_cycle_budget(
        event_count=event_count_for_cost,
        event_market_count=max_markets_for_any_event,
        config=cfg,
        historical=is_historical,
    )
    if not budget["withinBudget"]:
        return {
            "ok": False, "status": "BLOCKED_COST_GUARD", "version": VERSION,
            "budget": budget, "eventCount": len(raw_events), "discoveries": discoveries,
            "productionAuthorityChanged": False,
        }

    enriched: List[Dict[str, Any]] = []
    enrichment_errors: List[Dict[str, Any]] = []
    for event, markets in plans:
        event_id = str(event.get("id"))
        rows, errors = _fetch_event_markets_individually(event_id, markets, historical_at, cfg)
        enriched.extend(rows)
        enrichment_errors.extend(errors)

    normalized_featured = [v8.normalize_event(event) for event in raw_events]
    record = {
        "version": VERSION, "marketExpansionVersion": v8.VERSION,
        "collectedAtUtc": _now_iso(), "historicalAtUtc": historical_at,
        "contract": v8.shadow_contract(cfg), "budget": budget,
        "featuredHeaders": featured_headers, "featuredEvents": normalized_featured,
        "discoveries": discoveries, "eventEnrichment": enriched,
        "eventEnrichmentErrors": enrichment_errors,
        "selectedEventCount": len(plans), "selectedMarketRequestCount": total_selected_markets,
        "productionAuthorityChanged": False,
    }
    stamp = (historical_at or _now_iso()).replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    pointer = _put_immutable(f"mlb/odds-v8-shadow/{stamp}", record)
    return {
        "ok": True, "status": "COLLECTED_SHADOW", "version": VERSION,
        "eventCount": len(normalized_featured), "eventEnrichmentCount": len(enriched),
        "eventEnrichmentErrorCount": len(enrichment_errors), "budget": budget,
        "artifact": pointer, "productionAuthorityChanged": False,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, Mapping) else {}
    return collect_once(historical_at=event.get("historicalAtUtc"))
