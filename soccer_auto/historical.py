"""Resumable raw historical odds backfill.

Historical odds are archived but never labeled from prices.  The Odds API does
not return historical final results, so these rows stay outside supervised
training until an authoritative result is present.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping

from .canonical import digest, iso_utc, parse_utc
from .collector import _client, _market_keys_for_sport
from .config import (
    ALL_BOOKMAKER_REGIONS,
    FEATURED_GAME_MARKETS,
    HISTORICAL_ADDITIONAL_START,
    HISTORICAL_FEATURED_START,
    SOCCER_MARKET_SEEDS,
)
from .odds_api import DEFAULT_MAX_ATTEMPTS, chunks
from .storage import SoccerStore, ddb_safe, now_utc, plain


FEATURED_CALLS_PER_CYCLE = int(os.getenv("SOCCER_AUTO_HISTORICAL_FEATURED_CALLS_PER_CYCLE", "3"))
ADDITIONAL_EVENTS_PER_CYCLE = int(os.getenv("SOCCER_AUTO_HISTORICAL_ADDITIONAL_EVENTS_PER_CYCLE", "2"))
MARKETS_PER_REQUEST = int(os.getenv("SOCCER_AUTO_HISTORICAL_MARKETS_PER_REQUEST", "1"))


def _cursor(store: SoccerStore, name: str, start: str) -> dict[str, Any]:
    row = store.ops.get_item(Key={"PK": "HISTORICAL_CURSOR", "SK": name}, ConsistentRead=True).get("Item")
    return plain(row) if row else {
        "PK": "HISTORICAL_CURSOR",
        "SK": name,
        "entity_type": "SOCCER_HISTORICAL_BACKFILL_CURSOR",
        "competition_index": 0,
        "snapshot_at": start,
        "completed_competitions": [],
        "calls_completed": 0,
    }


def _save_cursor(store: SoccerStore, cursor: Mapping[str, Any]) -> None:
    store.ops.put_item(Item=ddb_safe(dict(cursor)))


def _competitions(store: SoccerStore) -> list[dict[str, Any]]:
    return sorted(
        [row for row in store.list_competitions() if not row.get("has_outrights")],
        key=lambda row: row["sport_key"],
    )


def _next_timestamp(wrapper: Mapping[str, Any], requested: str) -> str:
    if wrapper.get("next_timestamp"):
        return str(wrapper["next_timestamp"])
    current = parse_utc(str(wrapper.get("timestamp") or requested))
    interval = timedelta(minutes=10 if current < parse_utc("2022-09-18T00:00:00Z") else 5)
    return iso_utc(current + interval)


def _manifest(
    store: SoccerStore,
    *,
    mode: str,
    sport_key: str,
    requested_at: str,
    provider_at: str,
    raw_uri: str,
    payload_hash: str,
    event_id: str | None = None,
    markets: list[str] | None = None,
) -> None:
    identity = digest(
        {
            "mode": mode,
            "sport_key": sport_key,
            "provider_at": provider_at,
            "event_id": event_id,
            "markets": markets or [],
            "payload_hash": payload_hash,
        }
    )
    store.ops.put_item(
        Item=ddb_safe(
            {
                "PK": f"HISTORICAL_MANIFEST#{sport_key}",
                "SK": f"{provider_at}#{mode}#{identity}",
                "entity_type": "SOCCER_HISTORICAL_RAW_MANIFEST",
                "mode": mode,
                "sport_key": sport_key,
                "event_id": event_id,
                "markets": markets or [],
                "requested_at": requested_at,
                "provider_at": provider_at,
                "raw_uri": raw_uri,
                "payload_sha256": payload_hash,
                "supervised_label_status": "UNAVAILABLE_FROM_ODDS_API_HISTORICAL_ENDPOINTS",
                "training_eligible": False,
            }
        )
    )


def run_featured(store: SoccerStore) -> dict[str, Any]:
    client = _client()
    competitions = _competitions(store)
    cursor = _cursor(store, "FEATURED", HISTORICAL_FEATURED_START)
    completed = 0
    archived = []
    for _ in range(FEATURED_CALLS_PER_CYCLE):
        if not competitions:
            break
        index = int(cursor.get("competition_index") or 0) % len(competitions)
        competition = competitions[index]
        requested_at = str(cursor["snapshot_at"])
        observed_at = iso_utc(now_utc())
        if not store.provider_budget_available(
            "historical_featured",
            observed_at,
            estimated_cost=(
                DEFAULT_MAX_ATTEMPTS
                * 10
                * len(FEATURED_GAME_MARKETS)
                * len(ALL_BOOKMAKER_REGIONS)
            ),
        ):
            break
        if parse_utc(requested_at) >= now_utc() - timedelta(minutes=5):
            done = list(cursor.get("completed_competitions") or [])
            if competition["sport_key"] not in done:
                done.append(competition["sport_key"])
            cursor["completed_competitions"] = done
            cursor["competition_index"] = (index + 1) % len(competitions)
            cursor["snapshot_at"] = HISTORICAL_FEATURED_START
            continue
        response = client.historical_odds(
            competition["sport_key"],
            requested_at,
            FEATURED_GAME_MARKETS,
            regions=ALL_BOOKMAKER_REGIONS,
        )
        store.record_quota(response, operation="historical_featured", observed_at=observed_at)
        wrapper = response.data or {}
        provider_at = str(wrapper.get("timestamp") or requested_at)
        raw_uri, payload_hash = store.archive_json(
            "historical_featured",
            wrapper,
            observed_at=observed_at,
            identity=f"{competition['sport_key']}-{provider_at}",
            metadata={"sport_key": competition["sport_key"], "provider_at": provider_at},
        )
        _manifest(
            store,
            mode="FEATURED",
            sport_key=competition["sport_key"],
            requested_at=requested_at,
            provider_at=provider_at,
            raw_uri=raw_uri,
            payload_hash=payload_hash,
            markets=list(FEATURED_GAME_MARKETS),
        )
        cursor["snapshot_at"] = _next_timestamp(wrapper, requested_at)
        cursor["calls_completed"] = int(cursor.get("calls_completed") or 0) + 1
        cursor["updated_at"] = observed_at
        archived.append(raw_uri)
        completed += 1
    _save_cursor(store, cursor)
    return {"mode": "FEATURED", "calls": completed, "cursor": cursor, "archived": archived}


def _load_additional_snapshot(store: SoccerStore, cursor: dict[str, Any], competitions: list[dict[str, Any]]) -> None:
    client = _client()
    index = int(cursor.get("competition_index") or 0) % len(competitions)
    competition = competitions[index]
    requested_at = str(cursor["snapshot_at"])
    if parse_utc(requested_at) >= now_utc() - timedelta(minutes=5):
        cursor["competition_index"] = (index + 1) % len(competitions)
        cursor["snapshot_at"] = HISTORICAL_ADDITIONAL_START
        cursor["pending_events"] = []
        cursor["pending_event_index"] = 0
        return
    observed_at = iso_utc(now_utc())
    if not store.provider_budget_available(
        "historical_events",
        observed_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS,
    ):
        cursor["quota_deferred_at"] = observed_at
        return
    response = client.historical_events(competition["sport_key"], requested_at)
    store.record_quota(response, operation="historical_events", observed_at=observed_at)
    wrapper = response.data or {}
    provider_at = str(wrapper.get("timestamp") or requested_at)
    raw_uri, payload_hash = store.archive_json(
        "historical_events",
        wrapper,
        observed_at=observed_at,
        identity=f"{competition['sport_key']}-{provider_at}",
        metadata={"sport_key": competition["sport_key"], "provider_at": provider_at},
    )
    _manifest(
        store,
        mode="EVENTS",
        sport_key=competition["sport_key"],
        requested_at=requested_at,
        provider_at=provider_at,
        raw_uri=raw_uri,
        payload_hash=payload_hash,
    )
    cursor["pending_sport_key"] = competition["sport_key"]
    cursor["pending_provider_at"] = provider_at
    cursor["pending_requested_at"] = requested_at
    cursor["pending_next_timestamp"] = _next_timestamp(wrapper, requested_at)
    cursor["pending_events"] = [
        {key: event.get(key) for key in ("id", "sport_key", "commence_time", "home_team", "away_team")}
        for event in (wrapper.get("data") or [])
        if event.get("id")
    ]
    cursor["pending_event_index"] = 0
    cursor["pending_market_index"] = 0
    cursor["updated_at"] = observed_at


def run_additional(store: SoccerStore) -> dict[str, Any]:
    client = _client()
    competitions = _competitions(store)
    cursor = _cursor(store, "ADDITIONAL", HISTORICAL_ADDITIONAL_START)
    if not competitions:
        return {"mode": "ADDITIONAL", "calls": 0, "reason": "NO_COMPETITIONS"}
    if not cursor.get("pending_events"):
        _load_additional_snapshot(store, cursor, competitions)
    pending = list(cursor.get("pending_events") or [])
    index = int(cursor.get("pending_event_index") or 0)
    pending_market_index = int(cursor.get("pending_market_index") or 0)
    calls = 0
    archived = []
    events_completed_this_cycle = 0
    while index < len(pending) and events_completed_this_cycle < ADDITIONAL_EVENTS_PER_CYCLE:
        event = pending[index]
        sport_key = str(cursor["pending_sport_key"])
        provider_at = str(cursor["pending_provider_at"])
        market_keys = [
            key
            for key in _market_keys_for_sport(sport_key, SOCCER_MARKET_SEEDS)
            if key not in FEATURED_GAME_MARKETS
        ]
        market_batches = list(chunks(market_keys, MARKETS_PER_REQUEST))
        for market_offset in range(pending_market_index, len(market_batches)):
            market_batch = market_batches[market_offset]
            observed_at = iso_utc(now_utc())
            if not store.provider_budget_available(
                "historical_event_odds",
                observed_at,
                estimated_cost=(
                    DEFAULT_MAX_ATTEMPTS
                    * 10
                    * len(market_batch)
                    * len(ALL_BOOKMAKER_REGIONS)
                ),
            ):
                cursor["quota_deferred_at"] = observed_at
                cursor["pending_event_index"] = index
                cursor["pending_market_index"] = market_offset
                _save_cursor(store, cursor)
                return {"mode": "ADDITIONAL", "calls": calls, "cursor": cursor, "archived": archived, "deferred": True}
            response = client.historical_event_odds(
                sport_key,
                str(event["id"]),
                provider_at,
                market_batch,
                regions=ALL_BOOKMAKER_REGIONS,
            )
            store.record_quota(response, operation="historical_event_odds", observed_at=observed_at)
            wrapper = response.data or {}
            raw_uri, payload_hash = store.archive_json(
                "historical_event_odds",
                wrapper,
                observed_at=observed_at,
                identity=f"{sport_key}-{event['id']}-{provider_at}-{'-'.join(market_batch)}",
                metadata={"sport_key": sport_key, "event_id": str(event["id"]), "provider_at": provider_at},
            )
            _manifest(
                store,
                mode="ADDITIONAL",
                sport_key=sport_key,
                requested_at=str(cursor["pending_requested_at"]),
                provider_at=provider_at,
                raw_uri=raw_uri,
                payload_hash=payload_hash,
                event_id=str(event["id"]),
                markets=list(market_batch),
            )
            calls += 1
            archived.append(raw_uri)
            # Persist at market-batch granularity. A quota pause or later
            # invocation resumes this exact event at the first unfinished
            # market instead of replaying a large prefix forever.
            cursor["pending_event_index"] = index
            cursor["pending_market_index"] = market_offset + 1
            cursor["calls_completed"] = int(cursor.get("calls_completed") or 0) + 1
            cursor["updated_at"] = observed_at
            _save_cursor(store, cursor)
        index += 1
        events_completed_this_cycle += 1
        pending_market_index = 0
        cursor["pending_event_index"] = index
        cursor["pending_market_index"] = 0
        _save_cursor(store, cursor)
    cursor["pending_event_index"] = index
    if index >= len(pending):
        cursor["snapshot_at"] = cursor.get("pending_next_timestamp") or cursor["snapshot_at"]
        cursor["pending_events"] = []
        cursor["pending_event_index"] = 0
        cursor["pending_market_index"] = 0
        for key in ("pending_sport_key", "pending_provider_at", "pending_requested_at", "pending_next_timestamp"):
            cursor.pop(key, None)
    cursor["updated_at"] = iso_utc(now_utc())
    _save_cursor(store, cursor)
    return {"mode": "ADDITIONAL", "calls": calls, "cursor": cursor, "archived": archived}


def historical_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    mode = str((event or {}).get("mode") or "both").lower()
    result: dict[str, Any] = {"ok": True, "system": "soccer_auto"}
    if mode in {"both", "featured"}:
        result["featured"] = run_featured(store)
    if mode in {"both", "additional"}:
        result["additional"] = run_additional(store)
    return result
