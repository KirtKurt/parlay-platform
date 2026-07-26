"""Isolated V8 Odds API shadow collector.

This module is intentionally not the active V7 collector. It collects featured and
selected event-level markets, archives normalized evidence, and returns a bounded
shadow feature payload for later walk-forward comparison.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Tuple

import boto3

import mlb_odds_market_expansion_v8 as v8

VERSION = "MLB-ODDS-V8-SHADOW-COLLECTOR-v1"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BUCKET = os.environ.get("MLB_V8_SHADOW_BUCKET", "")
HTTP_TIMEOUT = max(5, int(os.environ.get("MLB_V8_HTTP_TIMEOUT_SECONDS", "20")))
_S3 = boto3.client("s3")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _put(key: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    if not BUCKET:
        raise RuntimeError("MLB_V8_SHADOW_BUCKET is not configured")
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response = _S3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
        Metadata={"record-type": "mlb_odds_v8_shadow", "version": VERSION},
    )
    return {"bucket": BUCKET, "key": key, "etag": str(response.get("ETag") or "").strip('"')}


def _events_from_featured(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
        payload = payload["data"]
    return [x for x in payload if isinstance(x, Mapping)] if isinstance(payload, list) else []


def collect_once(*, historical_at: str | None = None) -> Dict[str, Any]:
    cfg = v8.load_config()
    if not cfg.enabled:
        return {"ok": True, "status": "DISABLED", "version": VERSION, "contract": v8.shadow_contract(cfg)}
    if not API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")

    featured_raw, featured_headers = _get(v8.featured_odds_url(API_KEY, historical_at=historical_at, config=cfg))
    raw_events = _events_from_featured(featured_raw)
    normalized = [v8.normalize_event(event) for event in raw_events]
    selected_events = raw_events[: cfg.max_events_per_cycle]

    discoveries: List[Dict[str, Any]] = []
    requested_market_count = 0
    plans: List[Tuple[Mapping[str, Any], Tuple[str, ...]]] = []
    for event in selected_events:
        event_id = str(event.get("id") or "")
        if not event_id:
            continue
        available_raw, discovery_headers = _get(v8.event_markets_url(API_KEY, event_id))
        available = []
        if isinstance(available_raw, list):
            available = [str(x.get("key") or "") for x in available_raw if isinstance(x, Mapping)]
        elif isinstance(available_raw, Mapping):
            rows = available_raw.get("markets") or available_raw.get("data") or []
            available = [str(x.get("key") or x) for x in rows if isinstance(x, (str, Mapping))]
        selected = v8.selected_event_markets(available, cfg)
        requested_market_count = max(requested_market_count, len(selected))
        discoveries.append({
            "eventId": event_id,
            "availableMarkets": sorted(set(x for x in available if x)),
            "selectedMarkets": list(selected),
            "headers": discovery_headers,
        })
        if selected:
            plans.append((event, selected))

    budget = v8.enforce_cycle_budget(
        event_count=len(plans),
        event_market_count=requested_market_count,
        config=cfg,
    )
    if not budget["withinBudget"]:
        return {
            "ok": False,
            "status": "BLOCKED_COST_GUARD",
            "version": VERSION,
            "budget": budget,
            "eventCount": len(raw_events),
            "discoveries": discoveries,
        }

    enriched: List[Dict[str, Any]] = []
    for event, markets in plans:
        event_id = str(event.get("id"))
        raw, headers = _get(v8.event_odds_url(API_KEY, event_id, markets, historical_at=historical_at, config=cfg))
        payload = raw.get("data") if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else raw
        if not isinstance(payload, Mapping):
            continue
        normalized_event = v8.normalize_event(payload)
        enriched.append({
            "event": normalized_event,
            "features": v8.derive_team_level_features(normalized_event),
            "selectedMarkets": list(markets),
            "headers": headers,
        })

    stamp = (historical_at or _now_iso()).replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    record = {
        "version": VERSION,
        "marketExpansionVersion": v8.VERSION,
        "collectedAtUtc": _now_iso(),
        "historicalAtUtc": historical_at,
        "contract": v8.shadow_contract(cfg),
        "budget": budget,
        "featuredHeaders": featured_headers,
        "featuredEvents": normalized,
        "discoveries": discoveries,
        "eventEnrichment": enriched,
        "productionAuthorityChanged": False,
    }
    pointer = _put(f"mlb/odds-v8-shadow/{stamp}.json", record)
    return {
        "ok": True,
        "status": "COLLECTED_SHADOW",
        "version": VERSION,
        "eventCount": len(normalized),
        "eventEnrichmentCount": len(enriched),
        "budget": budget,
        "artifact": pointer,
        "productionAuthorityChanged": False,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, Mapping) else {}
    return collect_once(historical_at=event.get("historicalAtUtc"))
