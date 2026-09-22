"""Isolated V8 Odds API shadow collector.

This module never changes V7 authority. It collects featured and selected event-level
markets, archives content-addressed evidence, and exposes a read-only Lambda status
mode so deployment verification can prove scheduled advancement through the
collector's own least-privilege IAM role.
"""
from __future__ import annotations

import copy
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

VERSION = "MLB-ODDS-V8-SHADOW-COLLECTOR-v1.5"
API_KEY = os.environ.get("ODDS_API_KEY", "")
BUCKET = os.environ.get("MLB_V8_SHADOW_BUCKET", "")


def _safe_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_value(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


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
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("historicalAtUtc is invalid ISO8601") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("historicalAtUtc must be UTC")
    return text


def _get(url: str) -> Tuple[Any, Dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": VERSION},
    )
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
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
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
            Metadata={
                "record-type": "mlb_odds_v8_shadow",
                "version": VERSION,
                "sha256": digest,
            },
        )
        etag = str(response.get("ETag") or "").strip('"')
        created = True
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"PreconditionFailed", "412"}:
            raise
        etag = ""
        created = False
    return {
        "bucket": BUCKET,
        "key": key,
        "etag": etag,
        "sha256": digest,
        "created": created,
    }


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


def _affordable_event_limit(
    event_count: int,
    cfg: v8.V8Config,
    historical: bool,
) -> Tuple[int, Dict[str, Any]]:
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


def _book_identity(book: Mapping[str, Any], index: int) -> str:
    key = str(book.get("key") or "").strip()
    if key:
        return f"key:{key}"
    title = str(book.get("title") or "").strip()
    if title:
        return f"title:{title}"
    return f"index:{index}"


def _merge_normalized_events(
    base_event: Mapping[str, Any],
    additions: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Merge isolated market responses before deriving cross-market features."""
    merged = copy.deepcopy(dict(base_event))
    for addition in additions:
        for field in ("eventId", "homeTeam", "awayTeam"):
            left = str(merged.get(field) or "").strip()
            right = str(addition.get(field) or "").strip()
            if left and right and left != right:
                raise RuntimeError(f"V8_EVENT_IDENTITY_MISMATCH:{field}")
            if not left and right:
                merged[field] = addition.get(field)
        if not merged.get("commenceTime") and addition.get("commenceTime"):
            merged["commenceTime"] = addition.get("commenceTime")

        books = list(merged.get("bookmakers") or [])
        by_identity = {
            _book_identity(book, index): book
            for index, book in enumerate(books)
            if isinstance(book, Mapping)
        }
        for index, incoming in enumerate(addition.get("bookmakers") or []):
            if not isinstance(incoming, Mapping):
                continue
            identity = _book_identity(incoming, index)
            target = by_identity.get(identity)
            if target is None:
                copied = copy.deepcopy(dict(incoming))
                books.append(copied)
                by_identity[identity] = copied
                continue
            for metadata in ("key", "title", "sid", "lastUpdate"):
                if not target.get(metadata) and incoming.get(metadata):
                    target[metadata] = incoming.get(metadata)
            target_markets = target.setdefault("markets", {})
            for market_key, outcomes in (incoming.get("markets") or {}).items():
                target_markets[str(market_key)] = copy.deepcopy(outcomes)
        merged["bookmakers"] = books

    merged["version"] = v8.VERSION
    fingerprint_value = copy.deepcopy(merged)
    fingerprint_value.pop("fingerprint", None)
    merged["fingerprint"] = hashlib.sha256(
        json.dumps(
            fingerprint_value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return merged


def _fetch_event_markets_individually(
    base_event: Mapping[str, Any],
    markets: Sequence[str],
    historical_at: str | None,
    cfg: v8.V8Config,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    event_id = str(base_event.get("id") or "")
    normalized_base = v8.normalize_event(base_event)
    normalized_additions: List[Dict[str, Any]] = []
    successful_markets: List[str] = []
    headers_by_market: Dict[str, Dict[str, str]] = {}
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
            errors.append(
                {
                    "eventId": event_id,
                    "status": exc.code,
                    "markets": [market],
                    "errorType": "HTTPError",
                }
            )
            continue
        except (urllib.error.URLError, TimeoutError) as exc:
            errors.append(
                {
                    "eventId": event_id,
                    "status": "NETWORK_ERROR",
                    "markets": [market],
                    "errorType": type(exc).__name__,
                }
            )
            continue

        payload = (
            raw.get("data")
            if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping)
            else raw
        )
        if not isinstance(payload, Mapping):
            errors.append(
                {
                    "eventId": event_id,
                    "status": "MALFORMED_PAYLOAD",
                    "markets": [market],
                    "errorType": "PayloadValidation",
                }
            )
            continue
        normalized_additions.append(v8.normalize_event(payload))
        successful_markets.append(market)
        headers_by_market[market] = headers

    composite = _merge_normalized_events(normalized_base, normalized_additions)
    return (
        {
            "event": composite,
            "features": v8.derive_team_level_features(composite),
            "selectedMarkets": list(markets),
            "successfulMarkets": successful_markets,
            "failedMarketCount": len(errors),
            "headersByMarket": headers_by_market,
        },
        errors,
    )


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if value is None:
        return None
    return str(value)


def _list_latest_artifacts(limit: int = 20) -> List[Dict[str, Any]]:
    if not BUCKET:
        raise RuntimeError("MLB_V8_SHADOW_BUCKET is not configured")
    prefix = "mlb/odds-v8-shadow/"
    rows: List[Dict[str, Any]] = []
    token: str | None = None
    while True:
        kwargs: Dict[str, Any] = {
            "Bucket": BUCKET,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if token:
            kwargs["ContinuationToken"] = token
        response = _S3.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            if isinstance(item, Mapping) and item.get("Key"):
                rows.append(dict(item))
        token = str(response.get("NextContinuationToken") or "").strip() or None
        if not response.get("IsTruncated") or not token or len(rows) >= 5000:
            break

    rows.sort(
        key=lambda item: (
            _iso(item.get("LastModified")) or "",
            str(item.get("Key") or ""),
        ),
        reverse=True,
    )
    result: List[Dict[str, Any]] = []
    for item in rows[: max(1, limit)]:
        key = str(item.get("Key") or "")
        record: Dict[str, Any] = {}
        parse_error: str | None = None
        try:
            response = _S3.get_object(Bucket=BUCKET, Key=key)
            body = response["Body"].read()
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, Mapping):
                record = dict(parsed)
            else:
                parse_error = "ARTIFACT_NOT_OBJECT"
        except Exception as exc:  # status must expose, not hide, unreadable evidence
            parse_error = f"{type(exc).__name__}:{str(exc)[:200]}"

        expected_digest = key.rsplit("/", 1)[-1].removesuffix(".json")
        result.append(
            {
                "key": key,
                "lastModified": _iso(item.get("LastModified")),
                "size": item.get("Size"),
                "etag": str(item.get("ETag") or "").strip('"'),
                "collectedAtUtc": record.get("collectedAtUtc"),
                "historicalAtUtc": record.get("historicalAtUtc"),
                "triggerMode": record.get("triggerMode"),
                "authority": (record.get("contract") or {}).get("authority"),
                "withinBudget": (record.get("budget") or {}).get("withinBudget"),
                "estimatedCredits": (record.get("budget") or {}).get("estimatedCredits"),
                "productionAuthorityChanged": record.get("productionAuthorityChanged"),
                "sha256FromKey": expected_digest,
                "parseError": parse_error,
            }
        )
    return result


def shadow_status(limit: int = 20) -> Dict[str, Any]:
    cfg = v8.load_config()
    artifacts = _list_latest_artifacts(limit=limit)
    return {
        "ok": True,
        "status": "SHADOW_STATUS",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "contract": v8.shadow_contract(cfg),
        "bucket": BUCKET,
        "artifactCountReturned": len(artifacts),
        "latestArtifacts": artifacts,
        "productionAuthorityChanged": False,
    }


def collect_once(
    *,
    historical_at: str | None = None,
    trigger_mode: str = "manual",
) -> Dict[str, Any]:
    historical_at = _validate_historical_at(historical_at)
    trigger_mode = str(trigger_mode or "manual").strip()[:80] or "manual"
    cfg = v8.load_config()
    if not cfg.enabled:
        return {
            "ok": True,
            "status": "DISABLED",
            "version": VERSION,
            "triggerMode": trigger_mode,
            "contract": v8.shadow_contract(cfg),
            "productionAuthorityChanged": False,
        }
    if not API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")

    is_historical = historical_at is not None
    featured_raw, featured_headers = _get(
        v8.featured_odds_url(
            API_KEY,
            historical_at=historical_at,
            config=cfg,
        )
    )
    raw_events = _events_from_featured(featured_raw)
    affordable_count, pre_budget = _affordable_event_limit(
        len(raw_events),
        cfg,
        is_historical,
    )
    selected_events = raw_events[:affordable_count]

    if (
        affordable_count == 0
        and raw_events
        and pre_budget["featuredEstimatedCredits"] > pre_budget["maximumCredits"]
    ):
        return {
            "ok": False,
            "status": "BLOCKED_FEATURED_COST_GUARD",
            "version": VERSION,
            "triggerMode": trigger_mode,
            "budget": pre_budget,
            "eventCount": len(raw_events),
            "productionAuthorityChanged": False,
        }

    discoveries: List[Dict[str, Any]] = []
    plans: List[Tuple[Mapping[str, Any], Tuple[str, ...]]] = []
    total_selected_markets = 0
    for event in selected_events:
        event_id = str(event.get("id") or "")
        if not event_id:
            discoveries.append(
                {
                    "eventId": None,
                    "selectedMarkets": [],
                    "discoveryMode": "SKIPPED_MISSING_EVENT_ID",
                }
            )
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
                available_raw, discovery_headers = _get(
                    v8.event_markets_url(API_KEY, event_id, cfg)
                )
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
            except urllib.error.HTTPError as exc:
                selected = ()
                discoveries.append(
                    {
                        "eventId": event_id,
                        "availableMarkets": [],
                        "selectedMarkets": [],
                        "discoveryMode": "LIVE_EVENT_MARKETS_FAIL_SOFT",
                        "status": exc.code,
                        "errorType": "HTTPError",
                    }
                )
            except (urllib.error.URLError, TimeoutError) as exc:
                selected = ()
                discoveries.append(
                    {
                        "eventId": event_id,
                        "availableMarkets": [],
                        "selectedMarkets": [],
                        "discoveryMode": "LIVE_EVENT_MARKETS_FAIL_SOFT",
                        "status": "NETWORK_ERROR",
                        "errorType": type(exc).__name__,
                    }
                )
        plans.append((event, selected))
        total_selected_markets += len(selected)

    max_markets_for_any_event = max(
        (len(markets) for _, markets in plans),
        default=0,
    )
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
            "triggerMode": trigger_mode,
            "budget": budget,
            "eventCount": len(raw_events),
            "discoveries": discoveries,
            "productionAuthorityChanged": False,
        }

    enriched: List[Dict[str, Any]] = []
    enrichment_errors: List[Dict[str, Any]] = []
    successful_market_requests = 0
    for event, markets in plans:
        row, errors = _fetch_event_markets_individually(
            event,
            markets,
            historical_at,
            cfg,
        )
        enriched.append(row)
        enrichment_errors.extend(errors)
        successful_market_requests += len(row.get("successfulMarkets") or [])

    normalized_featured = [v8.normalize_event(event) for event in raw_events]
    collected_at = _now_iso()
    record = {
        "version": VERSION,
        "marketExpansionVersion": v8.VERSION,
        "collectedAtUtc": collected_at,
        "historicalAtUtc": historical_at,
        "triggerMode": trigger_mode,
        "contract": v8.shadow_contract(cfg),
        "budget": budget,
        "featuredHeaders": featured_headers,
        "featuredEvents": normalized_featured,
        "discoveries": discoveries,
        "eventEnrichment": enriched,
        "eventEnrichmentErrors": enrichment_errors,
        "selectedEventCount": len(plans),
        "selectedMarketRequestCount": total_selected_markets,
        "successfulMarketRequestCount": successful_market_requests,
        "affordableEventLimit": affordable_count,
        "productionAuthorityChanged": False,
    }
    stamp = (historical_at or collected_at).replace(":", "").replace("-", "").replace(
        "+", ""
    ).replace(".", "")
    pointer = _put_immutable(f"mlb/odds-v8-shadow/{stamp}", record)
    return {
        "ok": True,
        "status": "COLLECTED_SHADOW",
        "version": VERSION,
        "triggerMode": trigger_mode,
        "eventCount": len(normalized_featured),
        "eventEnrichmentCount": len(enriched),
        "eventEnrichmentErrorCount": len(enrichment_errors),
        "selectedMarketRequestCount": total_selected_markets,
        "successfulMarketRequestCount": successful_market_requests,
        "budget": budget,
        "affordableEventLimit": affordable_count,
        "artifact": pointer,
        "productionAuthorityChanged": False,
    }


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, Mapping) else {}
    mode = str(event.get("mode") or "manual").strip() or "manual"
    if mode.lower() in {"status", "shadow_status"}:
        return shadow_status(
            limit=_bounded_value(event.get("limit"), 20, 1, 100)
        )
    return collect_once(
        historical_at=event.get("historicalAtUtc"),
        trigger_mode=mode,
    )
