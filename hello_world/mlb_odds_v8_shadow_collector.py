"""Isolated V8 Odds API shadow collector.

This module never changes V7 authority. It collects featured and selected event-level
markets, archives content-addressed evidence, and returns bounded shadow features.
The bounded event-level window rotates deterministically every 15 minutes so every
slate event can receive first-five/alternate/team-market observations instead of
repeatedly enriching only the provider's first events.
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

VERSION = "MLB-ODDS-V8-SHADOW-COLLECTOR-v1.5-rotating-slate-window"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BUCKET = os.environ.get("MLB_V8_SHADOW_BUCKET", "")
ROTATION_INTERVAL_SECONDS = 15 * 60


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


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _validate_historical_at(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text.endswith("Z"):
        raise ValueError("historicalAtUtc must be UTC ISO8601 ending in Z")
    if _parse_utc(text) is None:
        raise ValueError("historicalAtUtc is invalid ISO8601")
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
            Bucket=BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
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
        rows = list(payload)
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
    if cfg.first_five_enabled:
        preferred.extend(v8.FIRST_FIVE_MARKETS)
    if cfg.alternates_enabled:
        preferred.extend(v8.ALTERNATE_MARKETS)
    if cfg.team_props_enabled:
        preferred.extend(v8.TEAM_PROP_MARKETS)
    if cfg.player_props_enabled:
        preferred.extend(v8.PLAYER_PROP_ALLOWLIST)
    return tuple(preferred[: cfg.max_event_markets])


def _affordable_event_limit(event_count: int, cfg: v8.V8Config, historical: bool) -> Tuple[int, Dict[str, Any]]:
    bounded = min(max(0, event_count), cfg.max_events_per_cycle)
    last = v8.enforce_cycle_budget(
        event_count=0,
        event_market_count=cfg.max_event_markets,
        config=cfg,
        historical=historical,
    )
    for count in range(bounded, -1, -1):
        budget = v8.enforce_cycle_budget(
            event_count=count,
            event_market_count=cfg.max_event_markets,
            config=cfg,
            historical=historical,
        )
        last = budget
        if budget["withinBudget"]:
            return count, budget
    return 0, last


def _event_order(event: Mapping[str, Any]) -> Tuple[str, str]:
    return (str(event.get("commence_time") or ""), str(event.get("id") or ""))


def _rotation_slot(*, historical_at: str | None, explicit_slot: Any = None) -> int:
    if explicit_slot not in (None, ""):
        try:
            return max(0, int(explicit_slot))
        except (TypeError, ValueError):
            raise ValueError("rotationSlot must be a non-negative integer")
    anchor = _parse_utc(historical_at) if historical_at else datetime.now(timezone.utc)
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    return int(anchor.timestamp()) // ROTATION_INTERVAL_SECONDS


def _rotating_window(
    raw_events: Sequence[Mapping[str, Any]],
    count: int,
    slot: int,
) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    ordered = sorted([row for row in raw_events if isinstance(row, Mapping)], key=_event_order)
    if not ordered or count <= 0:
        return [], {
            "version": "MLB-V8-ROTATING-WINDOW-v1",
            "rotationSlot": slot,
            "rotationOffset": 0,
            "windowSize": 0,
            "slateEventCount": len(ordered),
            "fullSlateCyclesRequired": 0,
            "selectedEventIds": [],
        }
    count = min(count, len(ordered))
    offset = (slot * count) % len(ordered)
    selected = [ordered[(offset + index) % len(ordered)] for index in range(count)]
    return selected, {
        "version": "MLB-V8-ROTATING-WINDOW-v1",
        "rotationSlot": slot,
        "rotationOffset": offset,
        "windowSize": count,
        "slateEventCount": len(ordered),
        "fullSlateCyclesRequired": (len(ordered) + count - 1) // count,
        "selectedEventIds": [str(row.get("id") or "") for row in selected],
    }


def _fetch_event_markets_individually(
    event_id: str,
    markets: Sequence[str],
    historical_at: str | None,
    cfg: v8.V8Config,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    enriched: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for market in markets:
        try:
            raw, headers = _get(
                v8.event_odds_url(
                    API_KEY,
                    event_id,
                    (market,),
                    historical_at=historical_at,
                    config=cfg,
                )
            )
        except urllib.error.HTTPError as exc:
            errors.append({"eventId": event_id, "status": exc.code, "markets": [market]})
            continue
        payload = raw.get("data") if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else raw
        if not isinstance(payload, Mapping):
            errors.append({"eventId": event_id, "status": "MALFORMED_PAYLOAD", "markets": [market]})
            continue
        normalized_event = v8.normalize_event(payload)
        enriched.append(
            {
                "event": normalized_event,
                "features": v8.derive_team_level_features(normalized_event),
                "selectedMarkets": [market],
                "headers": headers,
            }
        )
    return enriched, errors


def collect_once(*, historical_at: str | None = None, rotation_slot: Any = None) -> Dict[str, Any]:
    historical_at = _validate_historical_at(historical_at)
    cfg = v8.load_config()
    if not cfg.enabled:
        return {"ok": True, "status": "DISABLED", "version": VERSION, "contract": v8.shadow_contract(cfg)}
    if not API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")

    is_historical = historical_at is not None
    featured_raw, featured_headers = _get(v8.featured_odds_url(API_KEY, historical_at=historical_at, config=cfg))
    raw_events = _events_from_featured(featured_raw)
    affordable_count, pre_budget = _affordable_event_limit(len(raw_events), cfg, is_historical)
    slot = _rotation_slot(historical_at=historical_at, explicit_slot=rotation_slot)
    selected_events, rotation = _rotating_window(raw_events, affordable_count, slot)

    if affordable_count == 0 and raw_events and pre_budget["featuredEstimatedCredits"] > pre_budget["maximumCredits"]:
        return {
            "ok": False,
            "status": "BLOCKED_FEATURED_COST_GUARD",
            "version": VERSION,
            "budget": pre_budget,
            "eventCount": len(raw_events),
            "rotation": rotation,
            "productionAuthorityChanged": False,
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
            discoveries.append(
                {
                    "eventId": event_id,
                    "availableMarkets": [],
                    "selectedMarkets": list(selected),
                    "discoveryMode": "HISTORICAL_ALLOWLIST_NO_DISCOVERY_ENDPOINT",
                }
            )
        else:
            try:
                available_raw, discovery_headers = _get(v8.event_markets_url(API_KEY, event_id, cfg))
                available = _available_market_keys(available_raw)
                selected = v8.selected_event_markets(available, cfg)
                discoveries.append(
                    {
                        "eventId": event_id,
                        "availableMarkets": available,
                        "selectedMarkets": list(selected),
                        "headers": discovery_headers,
                        "discoveryMode": "LIVE_EVENT_MARKETS",
                    }
                )
            except Exception as exc:
                # Featured H2H/spread/total evidence remains useful even when an
                # event-market discovery endpoint is temporarily unavailable.
                selected = ()
                discoveries.append(
                    {
                        "eventId": event_id,
                        "availableMarkets": [],
                        "selectedMarkets": [],
                        "discoveryMode": "LIVE_EVENT_MARKETS_FAILED_SOFT",
                        "error": f"{type(exc).__name__}:{str(exc)[:240]}",
                    }
                )
        if selected:
            plans.append((event, selected))
            total_selected_markets += len(selected)

    max_markets_for_any_event = max((len(markets) for _, markets in plans), default=0)
    budget = v8.enforce_cycle_budget(
        event_count=len(plans),
        event_market_count=max_markets_for_any_event,
        config=cfg,
        historical=is_historical,
    )
    if not budget["withinBudget"]:
        return {
            "ok": False,
            "status": "BLOCKED_COST_GUARD",
            "version": VERSION,
            "budget": budget,
            "eventCount": len(raw_events),
            "discoveries": discoveries,
            "rotation": rotation,
            "productionAuthorityChanged": False,
        }

    enriched: List[Dict[str, Any]] = []
    enrichment_errors: List[Dict[str, Any]] = []
    for event, markets in plans:
        rows, errors = _fetch_event_markets_individually(str(event.get("id")), markets, historical_at, cfg)
        enriched.extend(rows)
        enrichment_errors.extend(errors)

    normalized_featured = [v8.normalize_event(event) for event in raw_events]
    record = {
        "version": VERSION,
        "marketExpansionVersion": v8.VERSION,
        "collectedAtUtc": _now_iso(),
        "historicalAtUtc": historical_at,
        "contract": v8.shadow_contract(cfg),
        "budget": budget,
        "rotation": rotation,
        "triggerMode": "historical_shadow" if is_historical else "scheduled_shadow",
        "featuredHeaders": featured_headers,
        "featuredEvents": normalized_featured,
        "discoveries": discoveries,
        "eventEnrichment": enriched,
        "eventEnrichmentErrors": enrichment_errors,
        "selectedEventCount": len(plans),
        "selectedMarketRequestCount": total_selected_markets,
        "affordableEventLimit": affordable_count,
        "productionAuthorityChanged": False,
    }
    stamp = (historical_at or _now_iso()).replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    pointer = _put_immutable(f"mlb/odds-v8-shadow/{stamp}", record)
    return {
        "ok": True,
        "status": "COLLECTED_SHADOW",
        "version": VERSION,
        "eventCount": len(normalized_featured),
        "eventEnrichmentCount": len(enriched),
        "eventEnrichmentErrorCount": len(enrichment_errors),
        "budget": budget,
        "rotation": rotation,
        "affordableEventLimit": affordable_count,
        "artifact": pointer,
        "productionAuthorityChanged": False,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, Mapping) else {}
    return collect_once(
        historical_at=event.get("historicalAtUtc"),
        rotation_slot=event.get("rotationSlot"),
    )
