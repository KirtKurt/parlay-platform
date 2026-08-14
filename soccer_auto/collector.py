"""Autonomous dynamic catalog, event, bookmaker, and market collection."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

import boto3

from .canonical import (
    digest,
    iso_utc,
    normalize_event_odds,
    parse_utc,
    schedule_identity,
    stable_event_key,
)
from .config import (
    ALL_BOOKMAKER_REGIONS,
    CADENCE_SECONDS_BY_HOURS_TO_START,
    FEATURED_GAME_MARKETS,
    PLAYER_MARKET_PREFIXES,
    PLAYER_PROP_COMPETITIONS,
    SOCCER_MARKET_SEEDS,
)
from .odds_api import (
    DEFAULT_MAX_ATTEMPTS,
    ApiResponse,
    OddsApiClient,
    OddsApiError,
    chunks,
)
from .schedule import (
    COLLECTION_LEAD_HOURS,
    DAY_TIMEZONE,
    collection_status,
    daily_collection_windows,
    stabilize_daily_collection_windows,
)
from .storage import SoccerStore, now_utc


JOB_VERSION = "soccer-auto-collection-job-v1"
MARKETS_PER_REQUEST = int(os.getenv("SOCCER_AUTO_MARKETS_PER_REQUEST", "10"))


class EventAlreadyStarted(RuntimeError):
    """A queued game-data job reached the worker at or after kickoff."""

    def __init__(self, event_key: str, commence_time: str, observed_at: str) -> None:
        super().__init__(
            f"soccer paid game-data collection blocked at/after kickoff: {commence_time}"
        )
        self.event_key = event_key
        self.commence_time = commence_time
        self.observed_at = observed_at


def _stabilize_windows(
    store: SoccerStore,
    windows: Mapping[str, Any],
) -> dict[str, Any]:
    persisted = [store.get_collection_window(match_day) for match_day in windows]
    return stabilize_daily_collection_windows(windows, persisted)


@lru_cache(maxsize=1)
def _api_key() -> str:
    direct = os.getenv("SOCCER_AUTO_ODDS_API_KEY", "").strip()
    if direct:
        return direct
    arn = os.environ.get("SOCCER_AUTO_ODDS_SECRET_ARN", "")
    if not arn:
        raise RuntimeError("SOCCER_AUTO_ODDS_SECRET_ARN is not configured")
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=arn).get("SecretString") or ""
    try:
        payload = json.loads(secret)
    except json.JSONDecodeError:
        return secret.strip()
    for key in ("api_key", "ODDS_API_KEY", "odds_api_key"):
        if payload.get(key):
            return str(payload[key]).strip()
    raise RuntimeError("soccer_auto Odds API secret contains no API key")


def _client() -> OddsApiClient:
    return OddsApiClient(_api_key())


def _observed_at() -> str:
    return iso_utc(now_utc())


def _current_window(store: SoccerStore, event: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    """Recalculate the gate from current event storage at provider-call time."""
    observed = parse_utc(observed_at)
    events = store.active_events_between(
        iso_utc(observed - timedelta(days=3)),
        iso_utc(observed + timedelta(days=45)),
    )
    events = _fresh_schedule_events(events, observed)
    windows = _stabilize_windows(store, daily_collection_windows(events))
    return collection_status(event, windows, observed_at=observed)


def _fresh_schedule_events(events: Iterable[Mapping[str, Any]], observed: datetime) -> list[Mapping[str, Any]]:
    """Exclude a future fixture after three consecutive inventory windows miss it."""
    fresh = []
    for event in events:
        last_seen = event.get("last_seen_at")
        commence = parse_utc(str(event["commence_time"]))
        if commence <= observed or not last_seen:
            fresh.append(event)
            continue
        if (observed - parse_utc(str(last_seen))).total_seconds() <= 45 * 60:
            fresh.append(event)
    return fresh


def _require_collection_window(store: SoccerStore, event: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    current = store.get_event(str(event["event_key"]))
    if not current:
        raise RuntimeError("soccer event metadata is unavailable for collection gate")
    if (
        str(current.get("event_id")) != str(event.get("event_id"))
        or iso_utc(str(current.get("commence_time"))) != iso_utc(str(event.get("commence_time")))
        or int(current.get("schedule_revision") or 1) != int(event.get("schedule_revision") or 1)
    ):
        raise RuntimeError("stale soccer collection job rejected after schedule revision")
    if parse_utc(observed_at) >= parse_utc(str(current["commence_time"])):
        raise EventAlreadyStarted(
            str(current["event_key"]),
            str(current["commence_time"]),
            observed_at,
        )
    status = _current_window(store, current, observed_at)
    if not status.get("open"):
        raise RuntimeError(
            f"soccer market/odds collection blocked before daily T-10 window: {status.get('opens_at')}"
        )
    return status


def _is_soccer(row: Mapping[str, Any]) -> bool:
    return str(row.get("group") or "").casefold() == "soccer" or str(row.get("key") or "").startswith("soccer_")


def _job(action: str, **payload: Any) -> dict[str, Any]:
    return {"version": JOB_VERSION, "action": action, **payload}


def _record_response(store: SoccerStore, response: ApiResponse, operation: str, observed_at: str) -> None:
    store.record_quota(response, operation=operation, observed_at=observed_at)


def catalog_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Refresh the free all-sports catalog and fan active soccer keys into SQS."""
    store = SoccerStore()
    observed_at = _observed_at()
    response = _client().sports(include_inactive=True)
    _record_response(store, response, "sports_all", observed_at)
    soccer_rows = [row for row in (response.data or []) if _is_soccer(row)]
    if not soccer_rows:
        raise RuntimeError("The Odds API all-sports response contained no soccer competitions")
    raw_uri, payload_hash = store.archive_json(
        "catalog",
        response.data,
        observed_at=observed_at,
        identity="all-soccer-sports",
        metadata={"operation": "sports_all"},
    )
    for row in soccer_rows:
        store.put_competition(row, observed_at)
    return {
        "ok": True,
        "system": "soccer_auto",
        "soccer_competitions": len(soccer_rows),
        "active_competitions_enqueued": 0,
        "fixture_discovery_authority": "soccer_auto.inventory_handler",
        "catalog_uri": raw_uri,
        "catalog_digest": payload_hash,
    }


def _cadence_seconds(commence_time: str, observed: datetime) -> int:
    hours = (parse_utc(commence_time) - observed).total_seconds() / 3600.0
    for upper_bound, seconds in CADENCE_SECONDS_BY_HOURS_TO_START:
        if hours <= upper_bound:
            return seconds
    return CADENCE_SECONDS_BY_HOURS_TO_START[-1][1]


def dispatch_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Dispatch odds work only after the daily first-kickoff T-10 gate opens."""
    store = SoccerStore()
    observed = now_utc()
    # Odds discovery is a pre-match responsibility. Completed-score recovery
    # has its own scores loop; dispatch must never keep paid event-market calls
    # running for already-started fixtures for up to three days.
    events = store.active_events_between(
        iso_utc(observed),
        iso_utc(observed + timedelta(days=45)),
    )
    events = _fresh_schedule_events(events, observed)
    windows = _stabilize_windows(store, daily_collection_windows(events))
    enqueued = 0
    skipped = 0
    before_window = 0
    for row in events:
        status = collection_status(row, windows, observed_at=observed)
        if not status["open"]:
            before_window += 1
            continue
        cadence = _cadence_seconds(row["commence_time"], observed)
        last = row.get("last_dispatched_at")
        if last and (observed - parse_utc(last)).total_seconds() < cadence:
            skipped += 1
            continue
        slot_epoch = int(observed.timestamp()) // cadence * cadence
        claim = f"DISCOVER_EVENT#{row['event_key']}#{slot_epoch}"
        if not store.claim_job(claim, slot_epoch + cadence * 2):
            skipped += 1
            continue
        try:
            store.enqueue(
                _job(
                    "DISCOVER_EVENT",
                    event={
                        key: row.get(key)
                        for key in (
                            "event_key", "event_id", "sport_key", "sport_title", "commence_time",
                            "home_team", "away_team", "schedule_revision",
                        )
                    },
                    cadence_seconds=cadence,
                    collection_window=status,
                )
            )
        except Exception:
            store.release_job(claim)
            raise
        store.mark_dispatched(row["event_key"], iso_utc(observed))
        enqueued += 1
    return {
        "ok": True,
        "system": "soccer_auto",
        "match_day_timezone": DAY_TIMEZONE,
        "daily_lead_hours": COLLECTION_LEAD_HOURS,
        "daily_windows": {key: value.__dict__ for key, value in windows.items()},
        "events_seen": len(events),
        "before_window": before_window,
        "enqueued": enqueued,
        "skipped": skipped,
    }


def inventory_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Refresh free fixture metadata outside the paid-odds work queue.

    Schedule discovery is the authority for the daily T-10 boundary. Keeping it
    out of the market fan-out queue prevents a large global slate from starving
    kickoff revisions or newly posted leagues.
    """
    store = SoccerStore()
    client = _client()
    observed = now_utc()
    slot_epoch = int(observed.timestamp()) // 900 * 900
    refreshed = 0
    skipped = 0
    failures = []
    for competition in store.list_competitions(active_only=True):
        sport_key = str(competition["sport_key"])
        claim = f"INVENTORY#{sport_key}#{slot_epoch}"
        if not store.claim_job(claim, slot_epoch + 1800):
            skipped += 1
            continue
        try:
            _discover_sport(
                store,
                client,
                _job(
                    "DISCOVER_SPORT",
                    sport_key=sport_key,
                    has_outrights=bool(competition.get("has_outrights")),
                    collect_outrights=False,
                ),
            )
            refreshed += 1
        except Exception as exc:
            store.release_job(claim)
            failures.append({"sport_key": sport_key, "error": str(exc)})
    return {
        "ok": not failures,
        "system": "soccer_auto",
        "fixture_inventory_only": True,
        "queue_bypassed": True,
        "competitions_refreshed": refreshed,
        "skipped": skipped,
        "failures": failures,
    }


def outright_dispatch_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect every active tournament outright product on its own cadence."""
    store = SoccerStore()
    observed = now_utc()
    slot_epoch = int(observed.timestamp()) // 300 * 300
    enqueued = 0
    for competition in store.list_competitions(active_only=True):
        if not competition.get("has_outrights"):
            continue
        sport_key = str(competition["sport_key"])
        claim = f"OUTRIGHTS#{sport_key}#{slot_epoch}"
        if not store.claim_job(claim, slot_epoch + 600):
            continue
        try:
            store.enqueue(_job("FETCH_OUTRIGHTS", sport_key=sport_key))
        except Exception:
            store.release_job(claim)
            raise
        enqueued += 1
    return {"ok": True, "system": "soccer_auto", "outright_jobs_enqueued": enqueued}


def _archive_discovery(
    store: SoccerStore,
    category: str,
    identity: str,
    response: ApiResponse,
    observed_at: str,
) -> str:
    _record_response(store, response, category, observed_at)
    uri, _ = store.archive_json(
        category,
        response.data,
        observed_at=observed_at,
        identity=identity,
        metadata={"operation": category},
    )
    return uri


def _discover_sport(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    sport_key = str(job["sport_key"])
    observed_at = _observed_at()
    outrights_enqueued = 0
    if job.get("has_outrights") and job.get("collect_outrights"):
        store.enqueue(_job("FETCH_OUTRIGHTS", sport_key=sport_key))
        outrights_enqueued = 1
    response = client.events(sport_key)
    uri = _archive_discovery(store, "events", sport_key, response, observed_at)
    events = response.data or []
    stored_count = 0
    for event in events:
        if not event.get("id") or not event.get("commence_time"):
            continue
        event = {**event, "sport_key": event.get("sport_key") or sport_key}
        store.put_event(event, observed_at)
        stored_count += 1
    return {
        "sport_key": sport_key,
        "events": len(events),
        "events_stored": stored_count,
        "odds_jobs_enqueued": 0,
        "outrights_enqueued": outrights_enqueued,
        "raw_uri": uri,
    }


def _market_keys_for_sport(sport_key: str, discovered: Iterable[str]) -> list[str]:
    discovered_keys = {str(key) for key in discovered if key}
    seed_keys = set(SOCCER_MARKET_SEEDS)
    if sport_key not in PLAYER_PROP_COMPETITIONS:
        # The published six-league list limits only proactive prop probes.  A
        # player market discovered at runtime in any other league is retained.
        seed_keys = {key for key in seed_keys if not key.startswith(PLAYER_MARKET_PREFIXES)}
    keys = discovered_keys | seed_keys
    keys.discard("outrights")
    keys.discard("outrights_lay")
    return sorted(keys)


def _discover_event(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(job["event"])
    observed_at = _observed_at()
    window = _require_collection_window(store, event, observed_at)
    inventory_by_book: dict[str, dict[str, Any]] = {}
    discovery_uris: list[str] = []
    # The event-market endpoint accepts comma-separated regions and costs one
    # credit per request. Query the complete current region catalog at once so
    # soccer receives the same bookmaker/market union without nine separate
    # calls competing with MLB and tennis for the shared subscription.
    if not store.provider_budget_available(
        "event_markets",
        observed_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS,
    ):
        return {
            "event_key": event["event_key"],
            "deferred": True,
            "reason": "SHARED_PROVIDER_QUOTA_RESERVE",
            "regions_completed": 0,
        }
    store.record_collection_window_call(event, window, observed_at)
    try:
        response = client.event_markets(
            event["sport_key"],
            event["event_id"],
            regions=ALL_BOOKMAKER_REGIONS,
        )
    except OddsApiError as exc:
        store.record_collection_failure(
            event_key=event["event_key"],
            operation="event_markets",
            observed_at=observed_at,
            detail=str(exc),
            scope={"regions": list(ALL_BOOKMAKER_REGIONS)},
            permanent=not exc.retryable,
        )
        if exc.retryable:
            raise
        return {
            "event_key": event["event_key"],
            "deferred": False,
            "reason": "EVENT_MARKET_INVENTORY_UNAVAILABLE",
            "regions_completed": 0,
        }
    discovery_uris.append(
        _archive_discovery(
            store,
            "event_markets",
            f"{event['sport_key']}-{event['event_id']}-all-regions",
            response,
            observed_at,
        )
    )
    payload = response.data or {}
    for book in payload.get("bookmakers") or []:
        book_key = str(book.get("key") or "").strip()
        if not book_key:
            continue
        target = inventory_by_book.setdefault(
            book_key,
            {"bookmaker": book_key, "title": book.get("title") or book_key, "regions": set(), "markets": set()},
        )
        # The combined response does not annotate a bookmaker's home region.
        # Persist the complete queried scope rather than inventing membership;
        # all later odds calls use this same all-region scope.
        target["regions"].update(ALL_BOOKMAKER_REGIONS)
        target["markets"].update(
            str(market.get("key"))
            for market in (book.get("markets") or [])
            if market.get("key")
        )

    for book_key, detail in store.cumulative_market_inventory(
        event["event_key"], observed_at=observed_at
    ).items():
        target = inventory_by_book.setdefault(
            book_key,
            {
                "bookmaker": book_key,
                "title": detail.get("title") or book_key,
                "regions": set(),
                "markets": set(),
            },
        )
        target["regions"].update(detail.get("regions") or ())
        target["markets"].update(detail.get("markets") or ())

    serializable_inventory = {
        book: {
            "title": row["title"],
            "regions": sorted(row["regions"]),
            "markets": sorted(row["markets"]),
        }
        for book, row in sorted(inventory_by_book.items())
    }
    store.put_market_inventory(event["event_key"], serializable_inventory, observed_at)
    store.put_coverage_plan(event["event_key"], serializable_inventory, observed_at)
    all_discovered = {
        market
        for row in inventory_by_book.values()
        for market in row["markets"]
    }
    all_markets = _market_keys_for_sport(event["sport_key"], all_discovered)
    books = sorted(inventory_by_book)
    enqueued = 0
    # Region mode is the provider-supported all-sportsbook request. One event
    # request spanning all nine current groups returns the same union of books
    # with far fewer HTTP calls than issuing ten-book batches. Returned books
    # are still reconciled individually against the cumulative inventory.
    for market_batch in chunks(all_markets, MARKETS_PER_REQUEST):
        store.enqueue(
            _job(
                "FETCH_EVENT",
                event=event,
                bookmakers=[],
                regions=list(ALL_BOOKMAKER_REGIONS),
                markets=list(market_batch),
                discovery_observed_at=observed_at,
            )
        )
        enqueued += 1
    return {
        "event_key": event["event_key"],
        "bookmakers": len(books),
        "discovered_markets": len(all_discovered),
        "market_scope": len(all_markets),
        "fetch_jobs_enqueued": enqueued,
        "discovery_uris": discovery_uris,
        "collection_window": window,
    }


def _fetch_event(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(job["event"])
    bookmakers = tuple(job.get("bookmakers") or ())
    regions = tuple(job.get("regions") or ALL_BOOKMAKER_REGIONS)
    markets = tuple(job["markets"])
    request_observed_at = _observed_at()
    window = _require_collection_window(store, event, request_observed_at)
    region_equivalents = max(1, (len(bookmakers) + 9) // 10) if bookmakers else max(1, len(regions))
    if not store.provider_budget_available(
        "event_odds",
        request_observed_at,
        estimated_cost=(
            DEFAULT_MAX_ATTEMPTS * max(1, len(markets)) * region_equivalents
        ),
    ):
        return {"event_key": event["event_key"], "deferred": True, "reason": "SHARED_PROVIDER_QUOTA_RESERVE"}
    store.record_collection_window_call(event, window, request_observed_at)
    try:
        response = client.event_odds(
            event["sport_key"],
            event["event_id"],
            markets,
            bookmakers=bookmakers or None,
            regions=regions,
        )
    except OddsApiError as exc:
        if exc.retryable:
            raise
        if len(markets) > 1:
            midpoint = max(1, len(markets) // 2)
            for subset in (markets[:midpoint], markets[midpoint:]):
                if subset:
                    store.enqueue({**dict(job), "markets": list(subset)})
            return {
                "event_key": event["event_key"],
                "split": True,
                "reason": "NONRETRYABLE_MARKET_BATCH_ERROR",
                "child_jobs": 2,
            }
        if len(bookmakers) > 1:
            midpoint = max(1, len(bookmakers) // 2)
            for subset in (bookmakers[:midpoint], bookmakers[midpoint:]):
                if subset:
                    store.enqueue({**dict(job), "bookmakers": list(subset)})
            return {
                "event_key": event["event_key"],
                "split": True,
                "reason": "NONRETRYABLE_BOOKMAKER_BATCH_ERROR",
                "child_jobs": 2,
            }
        if not bookmakers and len(regions) > 1:
            midpoint = max(1, len(regions) // 2)
            for subset in (regions[:midpoint], regions[midpoint:]):
                if subset:
                    store.enqueue({**dict(job), "regions": list(subset)})
            return {
                "event_key": event["event_key"],
                "split": True,
                "reason": "NONRETRYABLE_REGION_BATCH_ERROR",
                "child_jobs": 2,
            }
        store.record_collection_failure(
            event_key=event["event_key"],
            operation="event_odds",
            observed_at=request_observed_at,
            detail=str(exc),
            scope={"bookmakers": list(bookmakers), "regions": list(regions), "markets": list(markets)},
            permanent=True,
        )
        store.put_coverage_fetch(
            event["event_key"],
            {"bookmakers": []},
            observed_at=request_observed_at,
            requested_bookmakers=bookmakers,
            requested_markets=markets,
            plan_observed_at=str(job.get("discovery_observed_at") or "") or None,
        )
        return {
            "event_key": event["event_key"],
            "quarantined": True,
            "reason": "UNSUPPORTED_SINGLETON_BOOKMAKER_MARKET_SCOPE",
        }
    # This timestamp is evidence about when the response was actually in hand,
    # not when the request began.  Taking it only after event_odds returns keeps
    # a response crossing T-45 (or kickoff) from being backdated into a valid
    # pre-lock snapshot.
    observed_at = _observed_at()
    _record_response(store, response, "event_odds", observed_at)
    provider_raw_uri, provider_raw_digest = store.archive_json(
        "provider_event_odds_raw",
        response.data,
        observed_at=observed_at,
        identity=f"{event['sport_key']}-{event['event_id']}-{digest({'books': bookmakers, 'markets': markets})[:16]}",
        metadata={"event_key": event["event_key"], "operation": "event_odds"},
    )
    normalized = normalize_event_odds(response.data or {})
    current_event = store.get_event(str(event["event_key"]))
    try:
        queued_identity = str(event.get("schedule_identity") or schedule_identity(event))
        current_identity = str(
            (current_event or {}).get("schedule_identity")
            or schedule_identity(current_event or {})
        )
        response_identity = schedule_identity(normalized)
        identity_valid = queued_identity == current_identity == response_identity
        chronology_valid = parse_utc(observed_at) < min(
            parse_utc(str(event["commence_time"])),
            parse_utc(str((current_event or {})["commence_time"])),
            parse_utc(str(normalized["commence_time"])),
        )
    except (KeyError, TypeError, ValueError):
        identity_valid = False
        chronology_valid = False
    if not identity_valid or not chronology_valid:
        reason = (
            "PROVIDER_RESPONSE_SCHEDULE_IDENTITY_MISMATCH"
            if not identity_valid
            else "PROVIDER_RESPONSE_AT_OR_AFTER_KICKOFF"
        )
        store.record_collection_failure(
            event_key=event["event_key"],
            operation="event_odds_response_validation",
            observed_at=observed_at,
            detail=reason,
            scope={
                "bookmakers": list(bookmakers),
                "regions": list(regions),
                "markets": list(markets),
                "provider_raw_digest": provider_raw_digest,
            },
            permanent=False,
        )
        store.put_coverage_fetch(
            event["event_key"],
            {"bookmakers": []},
            observed_at=observed_at,
            requested_bookmakers=bookmakers,
            requested_markets=markets,
            plan_observed_at=str(job.get("discovery_observed_at") or "") or None,
        )
        return {
            "event_key": event["event_key"],
            "quarantined": True,
            "reason": reason,
            "provider_raw_uri": provider_raw_uri,
        }
    returned_inventory = {
        str(book.get("key")): {
            "title": book.get("title") or book.get("key"),
            "regions": list(regions) if not bookmakers else [],
            "markets": sorted(
                str(market.get("key"))
                for market in book.get("markets") or []
                if market.get("key")
            ),
        }
        for book in normalized.get("bookmakers") or []
        if book.get("key")
    }
    if returned_inventory:
        # Event-market inventory is a preview, not a complete catalog. Markets
        # first observed in an odds response become part of the next cycle's
        # cumulative per-book plan instead of being forgotten.
        store.put_market_inventory(event["event_key"], returned_inventory, observed_at)
    result = store.put_snapshot_attempt(
        event=event,
        payload=normalized,
        observed_at=observed_at,
        bookmakers=bookmakers or tuple(
            str(book.get("key")) for book in normalized.get("bookmakers") or []
        ),
        markets=markets,
        request_metadata={
            "regions": list(regions) if not bookmakers else [],
            "bookmakers": list(bookmakers),
            "markets": list(markets),
            "quota_last": response.quota_last,
            "quota_remaining": response.quota_remaining,
            "discovery_observed_at": job.get("discovery_observed_at"),
            "collection_window": window,
            "request_started_at": request_observed_at,
            "response_observed_at": observed_at,
            "provider_raw_uri": provider_raw_uri,
            "provider_raw_digest": provider_raw_digest,
        },
    )
    store.put_coverage_fetch(
        event["event_key"],
        normalized,
        observed_at=observed_at,
        requested_bookmakers=bookmakers,
        requested_markets=markets,
        plan_observed_at=str(job.get("discovery_observed_at") or "") or None,
    )
    result["returned_bookmakers"] = len(normalized.get("bookmakers") or [])
    return result


def _fetch_outrights(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    sport_key = str(job["sport_key"])
    observed_at = _observed_at()
    # Outrights are tournament-level products without a game-day kickoff and
    # are archived separately; they never enter game prediction training.
    if not store.provider_budget_available(
        "outrights",
        observed_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS * len(ALL_BOOKMAKER_REGIONS),
    ):
        return {"sport_key": sport_key, "deferred": True, "reason": "SHARED_PROVIDER_QUOTA_RESERVE"}
    try:
        response = client.odds(sport_key, ("outrights",), regions=ALL_BOOKMAKER_REGIONS)
    except Exception:
        # The dispatch claim otherwise suppresses a retry for the remainder of
        # this five-minute slot after a provider/network failure.
        slot_epoch = int(parse_utc(observed_at).timestamp()) // 300 * 300
        store.release_job(f"OUTRIGHTS#{sport_key}#{slot_epoch}")
        raise
    _record_response(store, response, "outrights", observed_at)
    uri, payload_hash = store.archive_json(
        "outrights",
        response.data,
        observed_at=observed_at,
        identity=sport_key,
        metadata={"sport_key": sport_key},
    )
    # A tournament outright object's commence_time is not a match kickoff.
    # Index its complete raw snapshot separately so it cannot move a daily T-10
    # window, enter the game dispatcher, freeze a T-45 vector, or be settled as
    # a match. This preserves all returned outright data for a future dedicated
    # tournament head without contaminating game learning.
    events = len(response.data or [])
    store.put_outright_manifest(
        sport_key=sport_key,
        observed_at=observed_at,
        raw_uri=uri,
        payload_hash=payload_hash,
        event_count=events,
    )
    return {
        "sport_key": sport_key,
        "events": events,
        "raw_uri": uri,
        "payload_digest": payload_hash,
        "schedule_planner_eligible": False,
    }


def process_job(job: Mapping[str, Any], *, store: SoccerStore | None = None, client: OddsApiClient | None = None) -> dict[str, Any]:
    if job.get("version") != JOB_VERSION:
        raise ValueError("unsupported soccer_auto collection job version")
    store = store or SoccerStore()
    client = client or _client()
    action = job.get("action")
    if action == "DISCOVER_SPORT":
        return _discover_sport(store, client, job)
    if action == "DISCOVER_EVENT":
        return _discover_event(store, client, job)
    if action == "FETCH_EVENT":
        return _fetch_event(store, client, job)
    if action == "FETCH_OUTRIGHTS":
        return _fetch_outrights(store, client, job)
    raise ValueError(f"unknown soccer_auto collection action: {action}")


def worker_handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    if event.get("Records"):
        store = SoccerStore()
        client = _client()
        failures = []
        processed = []
        for record in event["Records"]:
            job: Mapping[str, Any] = {}
            try:
                job = json.loads(record["body"])
                processed.append(process_job(job, store=store, client=client))
            except EventAlreadyStarted as exc:
                processed.append(
                    {
                        "event_key": exc.event_key,
                        "skipped": True,
                        "reason": "EVENT_ALREADY_STARTED",
                        "commence_time": exc.commence_time,
                        "observed_at": exc.observed_at,
                    }
                )
            except Exception as exc:
                job_event = job.get("event") or {}
                if job_event.get("event_key"):
                    try:
                        store.record_collection_failure(
                            event_key=str(job_event["event_key"]),
                            operation=str(job.get("action") or "worker"),
                            observed_at=_observed_at(),
                            detail=str(exc),
                            scope={"message_id": record.get("messageId")},
                            permanent=False,
                        )
                    except Exception:
                        pass
                print(
                    json.dumps(
                        {
                            "level": "ERROR",
                            "system": "soccer_auto",
                            "message_id": record.get("messageId"),
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )
                failures.append({"itemIdentifier": record.get("messageId")})
        return {"batchItemFailures": failures, "processed": processed}
    return process_job(event)
