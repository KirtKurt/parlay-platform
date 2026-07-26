#!/usr/bin/env python3
"""Run one read-only, pre-lock MLB V7 + V8 slate comparison.

V7 means the latest immutable historical optimizer candidate policy, even when that
candidate has not passed promotion. V8 means the corrected market-expansion code
applied as a shadow market-consensus read. The script never writes a prediction,
lock, champion, cutover, or wager. It publishes a research-only JSON report.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import boto3
import requests

import mlb_historical_policy_v1 as v7_policy
import mlb_odds_market_expansion_v8 as v8

EASTERN = ZoneInfo("America/New_York")
MAIN_STACK = "parlay-platform-dev"
HISTORICAL_STACK = "parlay-platform-mlb-historical-optimizer"
V8_STACK = "parlay-platform-mlb-odds-v8-shadow"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
SNAPSHOTS_TABLE = "parlay_platform_snapshots"
MAX_ONE_TIME_ESTIMATED_CREDITS = 150
HTTP_RETRIES = 5
HTTP_TIMEOUT_SECONDS = 25


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _safe_headers(headers: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            out[name] = int(raw)
        except Exception:
            out[name] = str(raw)
    return out


def _get_json(session: requests.Session, url: str) -> Tuple[Any, Dict[str, Any]]:
    last: Optional[Exception] = None
    for attempt in range(HTTP_RETRIES):
        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={"accept": "application/json", "user-agent": "INQSI-MLB-V7-V8-ONE-TIME-TEST-v1"},
            )
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt + 1 < HTTP_RETRIES:
                    time.sleep(min(12, 2**attempt))
                    continue
            response.raise_for_status()
            return response.json(), _safe_headers({str(k).lower(): v for k, v in response.headers.items()})
        except Exception as exc:
            last = exc
            if attempt + 1 >= HTTP_RETRIES:
                break
            time.sleep(min(12, 2**attempt))
    raise RuntimeError(f"HTTP request failed after bounded retries: {type(last).__name__}:{last}")


def _team_key(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    aliases = {
        "arizona d backs": "arizona diamondbacks",
        "d backs": "arizona diamondbacks",
        "athletics": "athletics",
        "oakland athletics": "athletics",
        "a s": "athletics",
        "la angels": "los angeles angels",
        "la dodgers": "los angeles dodgers",
        "ny mets": "new york mets",
        "ny yankees": "new york yankees",
        "tb rays": "tampa bay rays",
    }
    return aliases.get(text, text)


def _match_key(home: Any, away: Any) -> str:
    return f"{_team_key(home)}|{_team_key(away)}"


def _flatten_prediction(row: Mapping[str, Any]) -> Dict[str, Any]:
    nested = row.get("data")
    value = copy.deepcopy(dict(nested)) if isinstance(nested, Mapping) else {}
    for key, item in row.items():
        if key != "data" and key not in value:
            value[key] = copy.deepcopy(item)
    return value


def _prediction_rows(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("predictions") or payload.get("winner_predictions") or []
    return [_flatten_prediction(row) for row in rows if isinstance(row, Mapping)]


def _has_signals(row: Mapping[str, Any]) -> bool:
    return isinstance(row.get("homeSignal") or row.get("home_signal"), Mapping) and isinstance(
        row.get("awaySignal") or row.get("away_signal"), Mapping
    )


def _read_current_signal_rows(api_url: str, slate_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    session = requests.Session()
    endpoint = urljoin(api_url.rstrip("/") + "/", "v1/mlb/predictions")
    diagnostics: Dict[str, Any] = {"publicReadEndpoint": "/v1/mlb/predictions"}
    public_rows: List[Dict[str, Any]] = []
    try:
        response = session.get(
            endpoint,
            params={"date": slate_date, "limit": 500},
            timeout=30,
            headers={"accept": "application/json", "cache-control": "no-cache"},
        )
        diagnostics["publicReadStatusCode"] = response.status_code
        response.raise_for_status()
        payload = response.json()
        diagnostics["publicReadOk"] = payload.get("ok") is True
        diagnostics["publicReadModelVersion"] = payload.get("model_version") or payload.get("modelVersion")
        public_rows = [row for row in _prediction_rows(payload) if _has_signals(row)]
        diagnostics["publicRowsWithSignals"] = len(public_rows)
    except Exception as exc:
        diagnostics["publicReadError"] = f"{type(exc).__name__}:{str(exc)[:300]}"

    # A direct read-only engine calculation is a fallback only. It uses canonical
    # DynamoDB pull history and store=False, so it cannot mutate prediction state.
    fallback_rows: List[Dict[str, Any]] = []
    try:
        import mlb_game_winner_engine as engine

        fallback = engine.predict_all(slate_date, store=False, limit=500)
        fallback_rows = [row for row in fallback.get("predictions") or [] if _has_signals(row)]
        diagnostics["fallbackReadOk"] = fallback.get("ok") is True
        diagnostics["fallbackRowsWithSignals"] = len(fallback_rows)
        diagnostics["fallbackLatestPullAt"] = fallback.get("latestPullAt")
        diagnostics["fallbackUniquePullSlotCount"] = fallback.get("uniquePullSlotCount")
    except Exception as exc:
        diagnostics["fallbackReadError"] = f"{type(exc).__name__}:{str(exc)[:300]}"

    by_match: Dict[str, Dict[str, Any]] = {}
    for source, rows in (("PUBLIC_PERSISTED_PRELOCK_READ", public_rows), ("CANONICAL_PULL_HISTORY_READ_ONLY", fallback_rows)):
        for row in rows:
            home = row.get("homeTeam") or row.get("home_team")
            away = row.get("awayTeam") or row.get("away_team")
            if not home or not away:
                continue
            key = _match_key(home, away)
            if key not in by_match:
                value = copy.deepcopy(row)
                value["oneTimeSignalSource"] = source
                by_match[key] = value
    diagnostics["mergedRowsWithSignals"] = len(by_match)
    return list(by_match.values()), diagnostics


def _available_market_keys(payload: Any) -> List[str]:
    if isinstance(payload, list):
        rows = list(payload)
    elif isinstance(payload, Mapping):
        rows = list(payload.get("markets") or payload.get("data") or [])
        for book in payload.get("bookmakers") or []:
            if isinstance(book, Mapping):
                rows.extend(book.get("markets") or [])
    else:
        rows = []
    keys: List[str] = []
    for row in rows:
        if isinstance(row, str):
            keys.append(row)
        elif isinstance(row, Mapping) and row.get("key"):
            keys.append(str(row.get("key")))
    return sorted(set(key for key in keys if key))


def _merge_normalized_event(base: Mapping[str, Any], addition: Mapping[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    books: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for source in (base, addition):
        for book in source.get("bookmakers") or []:
            if not isinstance(book, Mapping):
                continue
            key = str(book.get("key") or book.get("title") or f"book-{len(order)}")
            if key not in books:
                books[key] = copy.deepcopy(dict(book))
                books[key]["markets"] = copy.deepcopy(dict(book.get("markets") or {}))
                order.append(key)
            else:
                books[key].setdefault("markets", {}).update(copy.deepcopy(dict(book.get("markets") or {})))
                for field in ("title", "sid", "lastUpdate"):
                    if book.get(field) is not None:
                        books[key][field] = copy.deepcopy(book.get(field))
    merged["bookmakers"] = [books[key] for key in order]
    return merged


def _today_v8_events(api_key: str, slate_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # Use the corrected V8 implementation, but limit event-level calls to the
    # directional first-five markets relevant to a winner comparison. Full-game
    # h2h, spreads, and totals are still collected for the complete slate.
    os.environ["MLB_V8_ENABLED"] = "true"
    os.environ["MLB_V8_FEATURED_REGIONS"] = "us"
    os.environ["MLB_V8_EVENT_REGIONS"] = "us,us2"
    os.environ["MLB_V8_FEATURED_MARKETS"] = "h2h,spreads,totals"
    os.environ["MLB_V8_FIRST_FIVE_ENABLED"] = "true"
    os.environ["MLB_V8_ALTERNATES_ENABLED"] = "false"
    os.environ["MLB_V8_TEAM_PROPS_ENABLED"] = "false"
    os.environ["MLB_V8_PLAYER_PROPS_ENABLED"] = "false"
    os.environ["MLB_V8_MAX_EVENT_MARKETS"] = "3"
    os.environ["MLB_V8_MAX_EVENTS_PER_CYCLE"] = "20"
    os.environ["MLB_V8_MAX_CREDITS_PER_CYCLE"] = str(MAX_ONE_TIME_ESTIMATED_CREDITS)
    cfg = v8.load_config()

    session = requests.Session()
    featured_raw, featured_headers = _get_json(session, v8.featured_odds_url(api_key, config=cfg))
    raw_rows = featured_raw.get("data") if isinstance(featured_raw, Mapping) else featured_raw
    raw_events = [row for row in raw_rows or [] if isinstance(row, Mapping)]
    selected_raw: List[Mapping[str, Any]] = []
    for row in raw_events:
        commence = _parse_dt(row.get("commence_time"))
        if commence and commence.astimezone(EASTERN).date().isoformat() == slate_date:
            selected_raw.append(row)

    maximum_estimated = (
        v8.estimate_featured_credits(cfg)
        + len(selected_raw)
        + len(selected_raw) * len(v8.FIRST_FIVE_MARKETS) * len(cfg.event_regions)
    )
    if maximum_estimated > MAX_ONE_TIME_ESTIMATED_CREDITS:
        raise RuntimeError(
            f"V8 one-time hard cost guard blocked {maximum_estimated} estimated credits "
            f"above {MAX_ONE_TIME_ESTIMATED_CREDITS}"
        )

    combined: Dict[str, Dict[str, Any]] = {
        str(row.get("id")): v8.normalize_event(row)
        for row in selected_raw
        if row.get("id")
    }
    errors: List[Dict[str, Any]] = []
    discoveries: List[Dict[str, Any]] = []
    discovery_attempts = 0
    event_market_attempts = 0
    event_market_successes = 0
    latest_headers: Dict[str, Any] = dict(featured_headers)

    for raw in selected_raw:
        event_id = str(raw.get("id") or "")
        if not event_id:
            continue
        discovery_attempts += 1
        try:
            discovery_raw, discovery_headers = _get_json(
                session, v8.event_markets_url(api_key, event_id, cfg)
            )
            latest_headers.update(discovery_headers)
            available = _available_market_keys(discovery_raw)
            selected_markets = [key for key in v8.FIRST_FIVE_MARKETS if key in set(available)]
            discoveries.append(
                {
                    "eventId": event_id,
                    "availableMarketCount": len(available),
                    "selectedMarkets": selected_markets,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "eventId": event_id,
                    "stage": "DISCOVERY",
                    "error": f"{type(exc).__name__}:{str(exc)[:220]}",
                }
            )
            continue

        for market in selected_markets:
            event_market_attempts += 1
            try:
                market_raw, market_headers = _get_json(
                    session,
                    v8.event_odds_url(api_key, event_id, (market,), config=cfg),
                )
                latest_headers.update(market_headers)
                payload = (
                    market_raw.get("data")
                    if isinstance(market_raw, Mapping) and isinstance(market_raw.get("data"), Mapping)
                    else market_raw
                )
                if not isinstance(payload, Mapping):
                    raise RuntimeError("event market payload is not an object")
                combined[event_id] = _merge_normalized_event(
                    combined.get(event_id) or v8.normalize_event(raw),
                    v8.normalize_event(payload),
                )
                event_market_successes += 1
            except Exception as exc:
                errors.append(
                    {
                        "eventId": event_id,
                        "stage": "EVENT_MARKET",
                        "market": market,
                        "error": f"{type(exc).__name__}:{str(exc)[:220]}",
                    }
                )

    estimated_credits = (
        v8.estimate_featured_credits(cfg)
        + discovery_attempts
        + event_market_attempts * len(cfg.event_regions)
    )
    if estimated_credits > MAX_ONE_TIME_ESTIMATED_CREDITS:
        raise RuntimeError("V8 actual one-time estimated credits exceeded the hard guard")

    rows: List[Dict[str, Any]] = []
    for raw in selected_raw:
        event_id = str(raw.get("id") or "")
        normalized = combined.get(event_id) or v8.normalize_event(raw)
        rows.append(
            {
                "event": normalized,
                "features": v8.derive_team_level_features(normalized),
            }
        )
    return rows, {
        "version": v8.VERSION,
        "marketSet": {
            "featured": list(cfg.featured_markets),
            "eventLevel": list(v8.FIRST_FIVE_MARKETS),
            "playerPropsEnabled": False,
        },
        "eventCount": len(rows),
        "discoveryAttempts": discovery_attempts,
        "eventMarketAttempts": event_market_attempts,
        "eventMarketSuccesses": event_market_successes,
        "errorCount": len(errors),
        "errors": errors,
        "estimatedCredits": estimated_credits,
        "maximumEstimatedCredits": MAX_ONE_TIME_ESTIMATED_CREDITS,
        "withinBudget": estimated_credits <= MAX_ONE_TIME_ESTIMATED_CREDITS,
        "providerQuota": latest_headers,
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
    }


def _feature(features: Mapping[str, Any], market: str, side: str, suffix: str) -> Optional[float]:
    key = f"{market}_{side.replace(' ', '_')}{suffix}"
    value = features.get(key)
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _devig_pair(home: Optional[float], away: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if home is None or away is None or home <= 0 or away <= 0 or home + away <= 0:
        return None, None
    total = home + away
    return home / total, away / total


def _v8_read(row: Mapping[str, Any]) -> Dict[str, Any]:
    event = row.get("event") or {}
    features = row.get("features") or {}
    home = str(event.get("homeTeam") or "")
    away = str(event.get("awayTeam") or "")
    full_home_raw = _feature(features, "h2h", home, "MedianImpliedProbability")
    full_away_raw = _feature(features, "h2h", away, "MedianImpliedProbability")
    full_home, full_away = _devig_pair(full_home_raw, full_away_raw)
    f5_home_raw = _feature(features, "h2h_1st_5_innings", home, "MedianImpliedProbability")
    f5_away_raw = _feature(features, "h2h_1st_5_innings", away, "MedianImpliedProbability")
    f5_home, f5_away = _devig_pair(f5_home_raw, f5_away_raw)
    home_spread = _feature(features, "spreads", home, "MedianPoint")
    away_spread = _feature(features, "spreads", away, "MedianPoint")
    f5_home_spread = _feature(features, "spreads_1st_5_innings", home, "MedianPoint")
    f5_away_spread = _feature(features, "spreads_1st_5_innings", away, "MedianPoint")

    if full_home is None or full_away is None:
        return {
            "available": False,
            "pick": None,
            "side": None,
            "probability": None,
            "reason": "V8 full-game two-sided H2H consensus unavailable",
            "features": features,
        }
    side = "home" if full_home >= full_away else "away"
    pick = home if side == "home" else away
    probability = full_home if side == "home" else full_away
    first_five_side = None
    first_five_pick = None
    if f5_home is not None and f5_away is not None:
        first_five_side = "home" if f5_home >= f5_away else "away"
        first_five_pick = home if first_five_side == "home" else away
    spread_side = None
    if home_spread is not None and away_spread is not None and home_spread != away_spread:
        spread_side = "home" if home_spread < away_spread else "away"
    confirmations = [value for value in (first_five_side, spread_side) if value]
    confirming = sum(value == side for value in confirmations)
    return {
        "available": True,
        "pick": pick,
        "side": side,
        "probability": probability,
        "probabilityPct": round(probability * 100.0, 2),
        "homeProbability": full_home,
        "awayProbability": full_away,
        "homeProbabilityPct": round(full_home * 100.0, 2),
        "awayProbabilityPct": round(full_away * 100.0, 2),
        "firstFivePick": first_five_pick,
        "firstFiveSide": first_five_side,
        "firstFiveHomeDirectionalProbabilityPct": round(f5_home * 100.0, 2) if f5_home is not None else None,
        "firstFiveAwayDirectionalProbabilityPct": round(f5_away * 100.0, 2) if f5_away is not None else None,
        "fullGameHomeSpreadMedian": home_spread,
        "fullGameAwaySpreadMedian": away_spread,
        "firstFiveHomeSpreadMedian": f5_home_spread,
        "firstFiveAwaySpreadMedian": f5_away_spread,
        "expandedMarketConfirmations": confirming,
        "expandedMarketChecksAvailable": len(confirmations),
        "confirmationStatus": (
            "CONFIRMED" if confirmations and confirming == len(confirmations)
            else "MIXED" if confirmations
            else "FULL_GAME_ONLY"
        ),
        "impliedLateInningRunEnvironment": features.get("impliedLateInningRunEnvironment"),
        "homeStarterBullpenSpreadDivergence": features.get("homeStarterBullpenSpreadDivergence"),
        "reason": "V8 shadow direction is the de-vigged median full-game H2H consensus; first-five and spread markets are confirmation diagnostics, not trained weights.",
        "features": features,
    }


def _v7_read(signal_row: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, Any]:
    home_signal = signal_row.get("homeSignal") or signal_row.get("home_signal") or {}
    away_signal = signal_row.get("awaySignal") or signal_row.get("away_signal") or {}
    selected, home_scored, away_scored = v7_policy.select_winner(home_signal, away_signal, policy)
    home_probability, away_probability = v7_policy.complementary_probabilities(home_scored, away_scored)
    side = str(selected.get("side") or "")
    home_team = str(signal_row.get("homeTeam") or signal_row.get("home_team") or home_scored.get("team") or "")
    away_team = str(signal_row.get("awayTeam") or signal_row.get("away_team") or away_scored.get("team") or "")
    if side not in {"home", "away"}:
        side = "home" if home_probability >= away_probability else "away"
    pick = str(selected.get("team") or (home_team if side == "home" else away_team))
    probability = home_probability if side == "home" else away_probability
    chosen = home_scored if side == "home" else away_scored
    return {
        "pick": pick,
        "side": side,
        "probability": probability,
        "probabilityPct": round(probability * 100.0, 2),
        "homeProbabilityPct": round(home_probability * 100.0, 2),
        "awayProbabilityPct": round(away_probability * 100.0, 2),
        "score": chosen.get("score"),
        "fairProbabilityPct": chosen.get("fairProbabilityPct"),
        "edgeVsBookPct": chosen.get("edgeVsBookPct"),
        "expectedValuePct": chosen.get("expectedValuePct"),
        "americanOdds": chosen.get("americanOdds"),
        "priceBook": chosen.get("priceBook"),
        "tags": chosen.get("tags") or [],
        "blockedReasons": chosen.get("blockedReasons") or [],
        "signalSource": signal_row.get("oneTimeSignalSource"),
        "sourcePullAtUtc": signal_row.get("predictionSourcePullAt") or signal_row.get("prediction_source_pull_at_utc"),
        "policyDigest": v7_policy.policy_digest(policy),
        "policyVersion": v7_policy.VERSION,
    }


def _load_v7_candidate(ddb: Any, s3: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    item = ddb.Table(SNAPSHOTS_TABLE).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
    ).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(item.get("data") or {})
    latest = state.get("latestExperiment") or {}
    pointer = latest.get("artifact") or {}
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    if not bucket or not key:
        raise RuntimeError("latest V7 experiment artifact pointer is missing")
    result = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8"))
    candidate = result.get("candidate") or {}
    policy = candidate.get("policy")
    if not isinstance(policy, Mapping):
        raise RuntimeError("latest V7 experiment artifact has no candidate policy")
    errors = v7_policy.validate_policy(policy)
    if errors:
        raise RuntimeError("latest V7 candidate policy is invalid: " + ",".join(errors))
    return copy.deepcopy(dict(policy)), {
        "experimentId": latest.get("experimentId"),
        "candidateStatus": latest.get("status") or result.get("status"),
        "artifact": pointer,
        "policyDigest": candidate.get("policyDigest") or v7_policy.policy_digest(policy),
        "promotionGate": result.get("promotionGate") or latest.get("promotionGate") or {},
        "optimizationRound": state.get("optimizationRound"),
        "optimizationCompletedAtUtc": state.get("optimizationCompletedAtUtc"),
        "eligibleGameCount": state.get("eligibleGameCount"),
        "completeSlateCount": state.get("completeSlateCount"),
        "phase": state.get("phase"),
        "authority": "REJECTED_CANDIDATE_TEST_ONLY",
        "productionAuthorityChanged": False,
    }


def run(slate_date: str, output: Path, region: str) -> Dict[str, Any]:
    started = _now()
    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_DEFAULT_REGION", region)
    os.environ.setdefault("SNAPSHOTS_TABLE", SNAPSHOTS_TABLE)
    os.environ.setdefault("TARGET_SNAPSHOTS_TABLE", SNAPSHOTS_TABLE)
    api_key = str(os.environ.get("ODDS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")

    cf = boto3.client("cloudformation", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    main_outputs = _outputs(cf, MAIN_STACK)
    historical_outputs = _outputs(cf, HISTORICAL_STACK)
    v8_outputs = _outputs(cf, V8_STACK)
    api_url = str(main_outputs.get("ApiUrl") or "")
    if not api_url:
        raise RuntimeError("main API URL output is missing")

    policy, v7_meta = _load_v7_candidate(ddb, s3)
    signal_rows, signal_diagnostics = _read_current_signal_rows(api_url, slate_date)
    signal_by_match = {
        _match_key(row.get("homeTeam") or row.get("home_team"), row.get("awayTeam") or row.get("away_team")): row
        for row in signal_rows
    }
    v8_rows, v8_meta = _today_v8_events(api_key, slate_date)

    games: List[Dict[str, Any]] = []
    missing_v7: List[str] = []
    missing_v8: List[str] = []
    lock_times: List[datetime] = []
    for v8_row in v8_rows:
        event = v8_row.get("event") or {}
        home = str(event.get("homeTeam") or "")
        away = str(event.get("awayTeam") or "")
        commence = _parse_dt(event.get("commenceTime"))
        if commence:
            lock_times.append(commence - timedelta(minutes=45))
        key = _match_key(home, away)
        signal_row = signal_by_match.get(key)
        v8_read = _v8_read(v8_row)
        if not v8_read.get("available"):
            missing_v8.append(f"{away} @ {home}")
        if signal_row is None:
            missing_v7.append(f"{away} @ {home}")
            continue
        v7_read = _v7_read(signal_row, policy)
        agreement = bool(v8_read.get("pick") and _team_key(v7_read.get("pick")) == _team_key(v8_read.get("pick")))
        games.append(
            {
                "gameId": event.get("eventId") or signal_row.get("gameId") or signal_row.get("game_id"),
                "awayTeam": away,
                "homeTeam": home,
                "matchup": f"{away} @ {home}",
                "commenceTimeUtc": _iso(commence) if commence else event.get("commenceTime"),
                "commenceTimeEt": commence.astimezone(EASTERN).isoformat() if commence else None,
                "v7Candidate": v7_read,
                "v8Shadow": {key: value for key, value in v8_read.items() if key != "features"},
                "v7V8Agreement": agreement,
                "combinedDecision": "V7_RETAINED_V8_CONFIRMS" if agreement else "V7_RETAINED_V8_CONFLICT",
                "oneTimeTestPrediction": v7_read.get("pick"),
                "oneTimeTestProbabilityPct": v7_read.get("probabilityPct"),
                "automaticWagerAllowed": False,
            }
        )

    games.sort(key=lambda row: str(row.get("commenceTimeUtc") or ""))
    completed = _now()
    earliest_lock = min(lock_times) if lock_times else None
    all_prelock = bool(earliest_lock and completed < earliest_lock)
    gate = v7_meta.get("promotionGate") or {}
    report = {
        "proofType": "MLB_V7_V8_ONE_TIME_TODAY_TEST",
        "version": "MLB-V7-V8-TODAY-TEST-v1",
        "createdAtUtc": _iso(completed),
        "captureStartedAtUtc": _iso(started),
        "slateDateEt": slate_date,
        "testOnly": True,
        "researchOnly": True,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "allDataCapturedBeforeEarliestGameTMinus45": all_prelock,
        "earliestGameLockAtUtc": _iso(earliest_lock) if earliest_lock else None,
        "sourceStacks": {
            "main": MAIN_STACK,
            "historical": HISTORICAL_STACK,
            "v8": V8_STACK,
            "historicalFunctionName": historical_outputs.get("HistoricalOptimizerFunctionName"),
            "v8FunctionName": v8_outputs.get("ShadowCollectorFunctionName"),
        },
        "v7": {
            **v7_meta,
            "promotionPassed": gate.get("passed") is True,
            "walkForwardMeanDailyAccuracy": gate.get("walkForwardMeanDailyAccuracy"),
            "walkForwardMinimumDailyAccuracy": gate.get("walkForwardMinimumDailyAccuracy"),
            "untouchedHoldoutMeanDailyAccuracy": gate.get("untouchedHoldoutMeanDailyAccuracy"),
            "untouchedHoldoutMinimumDailyAccuracy": gate.get("untouchedHoldoutMinimumDailyAccuracy"),
            "warning": "Latest immutable V7 candidate is applied for this one-time test despite failed promotion; it has no production authority.",
        },
        "v8": {
            **v8_meta,
            "warning": "V8 is an unpromoted shadow market-expansion read. Its probability is full-game market consensus, not a trained V8 model probability.",
        },
        "signalDiagnostics": signal_diagnostics,
        "gameCount": len(games),
        "v7PredictionCount": len(games),
        "v8PredictionCount": sum(bool(row.get("v8Shadow", {}).get("available")) for row in games),
        "agreementCount": sum(bool(row.get("v7V8Agreement")) for row in games),
        "missingV7": missing_v7,
        "missingV8": missing_v8,
        "games": games,
    }
    blockers: List[str] = []
    if len(v8_rows) != 15:
        blockers.append(f"today_v8_event_count_{len(v8_rows)}_expected_15")
    if len(games) != 15:
        blockers.append(f"complete_v7_prediction_count_{len(games)}_expected_15")
    if report["v8PredictionCount"] != 15:
        blockers.append(f"complete_v8_prediction_count_{report['v8PredictionCount']}_expected_15")
    if not all_prelock:
        blockers.append("one_time_capture_not_completed_before_earliest_t_minus_45")
    if not v8_meta.get("withinBudget"):
        blockers.append("v8_one_time_cost_guard_failed")
    report["blockers"] = blockers
    report["ok"] = not blockers
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="Eastern MLB slate date, YYYY-MM-DD")
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(args.date, Path(args.output), args.region)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
