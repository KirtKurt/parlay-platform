"""Autonomous dynamic catalog, event, bookmaker, and market collection."""
from __future__ import annotations

import json
import os
import uuid
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
from .storage import (
    COVERAGE_EXTERNAL_QUOTA_REASONS,
    COVERAGE_MARKETS_PER_REQUEST,
    COVERAGE_PLAN_VERSION,
    SoccerStore,
    coverage_batch_digest,
    now_utc,
)


JOB_VERSION = "soccer-auto-collection-job-v1"
MARKETS_PER_REQUEST = COVERAGE_MARKETS_PER_REQUEST


class EventAlreadyStarted(RuntimeError):
    """A queued game-data job reached the worker at or after kickoff."""

    def __init__(self, event_key: str, commence_time: str, observed_at: str) -> None:
        super().__init__(
            f"soccer paid game-data collection blocked at/after kickoff: {commence_time}"
        )
        self.event_key = event_key
        self.commence_time = commence_time
        self.observed_at = observed_at


class ProviderBudgetDeferred(RuntimeError):
    """Paid provider work was not attempted and needs a delayed delivery."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(str(result.get("reason") or "SHARED_PROVIDER_QUOTA_RESERVE"))
        self.result = dict(result)


class CoverageExecutionDeferred(RuntimeError):
    """Another worker owns the short execution lease for this exact batch."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        super().__init__(str(result.get("reason") or "COVERAGE_BATCH_LEASE_BUSY"))
        self.result = dict(result)


def _payload_pair_keys(payload: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            f"{book.get('key')}|{market.get('key')}"
            for book in payload.get("bookmakers") or []
            if book.get("key")
            for market in book.get("markets") or []
            if market.get("key")
        }
    )


def _planned_pairs_for_scope(
    pairs: Iterable[str],
    *,
    markets: Iterable[str],
    bookmakers: Iterable[str] = (),
) -> list[str]:
    market_scope = {str(value) for value in markets if value}
    book_scope = {str(value) for value in bookmakers if value}
    return sorted(
        {
            str(pair)
            for pair in pairs
            if "|" in str(pair)
            and str(pair).rsplit("|", 1)[1] in market_scope
            and (not book_scope or str(pair).rsplit("|", 1)[0] in book_scope)
        }
    )


def _coverage_batch_digest(job: Mapping[str, Any]) -> str:
    return coverage_batch_digest(
        plan_digest=str(job.get("plan_digest") or ""),
        markets=tuple(job.get("markets") or ()),
        bookmakers=tuple(job.get("bookmakers") or ()),
        regions=tuple(job.get("regions") or ()),
        planned_pairs=tuple(job.get("planned_pairs") or ()),
        split_group_digest=str(job.get("split_group_digest") or ""),
        split_expected_regions=tuple(
            job.get("split_expected_regions") or ()
        ),
        split_leaf_id=str(job.get("split_leaf_id") or ""),
        split_expected_leaf_ids=tuple(
            job.get("split_expected_leaf_ids") or ()
        ),
    )


def _coverage_leaf_scope_digest(job: Mapping[str, Any]) -> str:
    return digest(
        {
            "plan_digest": str(job.get("plan_digest") or ""),
            "markets": list(job.get("markets") or ()),
            "bookmakers": list(job.get("bookmakers") or ()),
            "regions": list(job.get("regions") or ()),
            "planned_pairs": sorted(
                str(pair) for pair in job.get("planned_pairs") or ()
            ),
        }
    )


def _fetch_child_job(
    job: Mapping[str, Any],
    *,
    markets: Iterable[str] | None = None,
    bookmakers: Iterable[str] | None = None,
    regions: Iterable[str] | None = None,
) -> dict[str, Any]:
    child = dict(job)
    if markets is not None:
        child["markets"] = list(markets)
    if bookmakers is not None:
        child["bookmakers"] = list(bookmakers)
    if regions is not None:
        child["regions"] = list(regions)
    child["planned_pairs"] = _planned_pairs_for_scope(
        job.get("planned_pairs") or (),
        markets=child.get("markets") or (),
        bookmakers=child.get("bookmakers") or (),
    )
    child["batch_digest"] = _coverage_batch_digest(child)
    return child


def _bind_split_children(
    parent: Mapping[str, Any], children: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Bind deterministic leaf lineage for any split axis."""
    root = str(
        parent.get("split_group_digest") or parent.get("batch_digest") or ""
    )
    parent_leaf = str(parent.get("split_leaf_id") or "")
    bound = [dict(child) for child in children]
    child_leaf_ids = [_coverage_leaf_scope_digest(child) for child in bound]
    expected = set(parent.get("split_expected_leaf_ids") or ())
    if parent_leaf:
        expected.discard(parent_leaf)
    expected.update(child_leaf_ids)
    for child, leaf_id in zip(bound, child_leaf_ids):
        child["split_group_digest"] = root
        child["split_leaf_id"] = leaf_id
        child["split_expected_leaf_ids"] = sorted(expected)
        child["batch_digest"] = _coverage_batch_digest(child)
    return bound


def _coverage_fetch_jobs(
    event: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    plan_digest = str(plan.get("plan_digest") or "")
    plan_observed_at = str(plan.get("plan_observed_at") or "")
    expected_pairs = sorted(
        set(plan.get("expected_pairs") or ())
        or set(plan.get("required_pairs") or ()) | set(plan.get("probe_pairs") or ())
    )
    jobs: list[dict[str, Any]] = []
    for market_batch in chunks(
        list(plan.get("request_markets") or ()),
        MARKETS_PER_REQUEST,
    ):
        fetch_job = _job(
            "FETCH_EVENT",
            event=dict(event),
            bookmakers=[],
            regions=list(ALL_BOOKMAKER_REGIONS),
            markets=list(market_batch),
            discovery_observed_at=plan_observed_at,
            coverage_generation_at=str(plan.get("discovery_observed_at") or ""),
            plan_digest=plan_digest,
            planned_pairs=_planned_pairs_for_scope(
                expected_pairs,
                markets=market_batch,
            ),
        )
        fetch_job["batch_digest"] = _coverage_batch_digest(fetch_job)
        jobs.append(fetch_job)
    return jobs


def _enqueue_coverage_fanout(
    store: SoccerStore,
    event: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    event_key = str(event["event_key"])
    plan_observed_at = str(plan.get("plan_observed_at") or "")
    plan_digest = str(plan.get("plan_digest") or "")
    jobs = _coverage_fetch_jobs(event, plan)
    if not jobs:
        raise RuntimeError("coverage plan produced no fetch batches")
    fanout = store.put_coverage_fanout_expected(
        event_key,
        plan_observed_at=plan_observed_at,
        plan_digest=plan_digest,
        batch_digests=[str(job["batch_digest"]) for job in jobs],
        observed_at=observed_at,
    )
    if not fanout.get("latest_summary_updated"):
        raise RuntimeError("coverage fanout plan conflicts with its immutable generation")
    already_enqueued = set(fanout.get("fanout_enqueued_batch_digests") or ())
    enqueued = 0
    for fetch_job in jobs:
        batch_digest = str(fetch_job["batch_digest"])
        if batch_digest in already_enqueued:
            continue
        # SQS and DynamoDB cannot be committed atomically. Send first, then
        # persist evidence. A crash between them may resend the same batch;
        # FETCH_EVENT's execution claim makes that duplicate provider-idempotent.
        store.enqueue(fetch_job)
        marked = store.mark_coverage_fanout_enqueued(
            event_key,
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
            batch_digest=batch_digest,
            observed_at=observed_at,
        )
        if not marked.get("latest_summary_updated"):
            raise RuntimeError("coverage fetch enqueue evidence lost its plan generation")
        enqueued += 1
    completed = store.complete_coverage_fanout(
        event_key,
        plan_observed_at=plan_observed_at,
        plan_digest=plan_digest,
        observed_at=observed_at,
    )
    if not completed.get("latest_summary_updated"):
        raise RuntimeError("coverage fetch fanout is not durably complete")
    return {
        "fetch_jobs_enqueued": enqueued,
        "fetch_jobs_total": len(jobs),
        "fanout_resumed": bool(already_enqueued),
    }


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
    events, inventory_authority = store.authoritative_active_events_between(
        iso_utc(observed - timedelta(days=3)),
        iso_utc(observed + timedelta(days=45)),
        observed_at=observed_at,
    )
    if not inventory_authority.get("valid"):
        raise CoverageExecutionDeferred(
            {
                "event_key": str(event.get("event_key") or ""),
                "deferred": True,
                "reason": "EVENT_INVENTORY_AUTHORITY_UNAVAILABLE",
                "authority_reason": str(
                    inventory_authority.get("reason") or ""
                ),
                "external_capacity": False,
            }
        )
    events = _fresh_schedule_events(events, observed)
    windows = _stabilize_windows(store, daily_collection_windows(events))
    return collection_status(event, windows, observed_at=observed)


def _fresh_schedule_events(events: Iterable[Mapping[str, Any]], observed: datetime) -> list[Mapping[str, Any]]:
    """Exclude a future fixture after three consecutive inventory windows miss it."""
    fresh = []
    for event in events:
        commence = parse_utc(str(event["commence_time"]))
        if commence <= observed:
            fresh.append(event)
            continue
        # Wall-clock age is not omission evidence: an endpoint/network outage
        # refreshes nothing. Only three successful scoped responses that each
        # excluded this event can retire it from the dispatch universe.
        if int(event.get("inventory_omission_count") or 0) < 3:
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


def _provider_budget_admission(
    store: SoccerStore,
    operation: str,
    observed_at: str,
    *,
    estimated_cost: int,
) -> dict[str, Any]:
    """Preserve the exact guard cause instead of collapsing every denial to quota."""
    detailed = getattr(store, "provider_budget_admission", None)
    if callable(detailed):
        result = dict(
            detailed(
                operation,
                observed_at,
                estimated_cost=estimated_cost,
            )
            or {}
        )
        reason = str(result.get("reason") or "QUOTA_OBSERVATION_UNAVAILABLE")
        result["reason"] = reason
        result["available"] = bool(result.get("available"))
        result["external_capacity"] = bool(
            not result["available"]
            and reason in COVERAGE_EXTERNAL_QUOTA_REASONS
        )
        return result
    # Test doubles and older isolated callers expose only the historical bool.
    # A false legacy result models the documented reserve denial; production
    # always takes the detailed branch above.
    available = bool(
        store.provider_budget_available(
            operation,
            observed_at,
            estimated_cost=estimated_cost,
        )
    )
    return {
        "available": available,
        "reason": "ADMITTED" if available else "SHARED_SUBSCRIPTION_RESERVE_REACHED",
        "external_capacity": not available,
    }


def _catalog_snapshot(
    store: SoccerStore,
    client: OddsApiClient,
    *,
    observed_at: str,
    persist: bool,
) -> dict[str, Any]:
    """Read the free catalog; mutate authority only inside inventory's lease."""
    response = client.sports(include_inactive=True)
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
    if persist:
        for row in soccer_rows:
            store.put_competition(row, observed_at)
    return {
        "soccer_competitions": len(soccer_rows),
        "soccer_competition_keys": sorted(str(row["key"]) for row in soccer_rows),
        "catalog_uri": raw_uri,
        "catalog_digest": payload_hash,
        "persisted": bool(persist),
    }


def catalog_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Observe the free catalog; inventory owns the fenced registry mutation."""
    store = SoccerStore()
    snapshot = _catalog_snapshot(
        store,
        _client(),
        observed_at=_observed_at(),
        persist=False,
    )
    return {
        "ok": True,
        "system": "soccer_auto",
        **snapshot,
        "active_competitions_enqueued": 0,
        "catalog_observational_only": True,
        "fixture_discovery_authority": "soccer_auto.inventory_handler",
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
    events, inventory_authority = store.authoritative_active_events_between(
        iso_utc(observed),
        iso_utc(observed + timedelta(days=45)),
        observed_at=iso_utc(observed),
    )
    events = _fresh_schedule_events(events, observed)
    windows = _stabilize_windows(store, daily_collection_windows(events))
    enqueued = 0
    skipped = 0
    before_window = 0
    schedule_races = 0
    recovered_plans = 0
    prepared: list[dict[str, Any]] = []
    for row in events:
        status = collection_status(row, windows, observed_at=observed)
        if not status["open"]:
            before_window += 1
            continue
        cadence = _cadence_seconds(row["commence_time"], observed)
        last = row.get("last_dispatched_at")
        latest_summary = store.latest_coverage_summary(str(row["event_key"]))
        event_identity = str(row.get("schedule_identity") or schedule_identity(row))
        summary_current = bool(
            latest_summary.get("entity_type") == "SOCCER_EVENT_COVERAGE_LATEST"
            and str(latest_summary.get("plan_version") or "")
            == COVERAGE_PLAN_VERSION
            and int(latest_summary.get("schedule_revision") or 0)
            == int(row.get("schedule_revision") or 0)
            and str(latest_summary.get("schedule_identity") or "") == event_identity
        )
        due = (
            not summary_current
            or not last
            or (observed - parse_utc(last)).total_seconds() >= cadence
        )
        # A discovery worker can persist an immutable plan and then fail while
        # publishing its multi-batch fanout.  Its SQS delivery is invisible for
        # up to 15 minutes, so recover that exact generation on the next
        # dispatcher tick.  The discovery worker detects the saved plan and
        # resumes fanout without another paid event-markets request.
        recovery_generation = str(
            latest_summary.get("discovery_observed_at") or ""
        )
        recoverable_plan = bool(
            not due
            and summary_current
            and str(latest_summary.get("discovery_status") or "")
            in {
                "QUEUED",
                "PLAN_READY",
                "FANOUT_PENDING",
                "STARTED",
                "ENQUEUE_FAILED",
            }
            and recovery_generation
            and latest_summary.get("plan_observed_at")
            and latest_summary.get("plan_digest")
            and latest_summary.get("request_markets")
            and not latest_summary.get("coverage_error")
        )
        dispatch_required = bool(due or recoverable_plan)
        if recoverable_plan:
            generation_at = recovery_generation
            slot_epoch = int(parse_utc(recovery_generation).timestamp())
        else:
            generation_source = observed if due else parse_utc(str(last))
            slot_epoch = int(generation_source.timestamp()) // cadence * cadence
            generation_at = iso_utc(
                datetime.fromtimestamp(slot_epoch, timezone.utc)
            )
        prepared.append(
            {
                "row": row,
                "status": status,
                "cadence": cadence,
                "slot_epoch": slot_epoch,
                "generation_at": generation_at,
                "due": due,
                "dispatch_required": dispatch_required,
                "recoverable_plan": recoverable_plan,
                "manifest": {
                    "event_key": row["event_key"],
                    "commence_time": row["commence_time"],
                    "schedule_revision": int(row.get("schedule_revision") or 0),
                    "schedule_identity": event_identity,
                    "required_discovery_observed_at": (
                        generation_at if dispatch_required else ""
                    ),
                },
            }
        )
    dispatch_observed_at = iso_utc(observed)
    manifest = store.put_coverage_dispatch_manifest(
        [item["manifest"] for item in prepared],
        observed_at=dispatch_observed_at,
        inventory_authority=inventory_authority,
    )
    if not manifest.get("latest_manifest_updated"):
        return {
            "ok": True,
            "system": "soccer_auto",
            "stale_dispatch": True,
            "observed_at": dispatch_observed_at,
            "events_seen": len(events),
            "before_window": before_window,
            "enqueued": 0,
            "skipped": len(prepared),
        }
    if not inventory_authority.get("valid"):
        return {
            "ok": True,
            "system": "soccer_auto",
            "authority_deferred": True,
            "authority_reason": str(inventory_authority.get("reason") or ""),
            "inventory_generation_id": str(
                inventory_authority.get("generation_id") or ""
            ),
            "observed_at": dispatch_observed_at,
            "coverage_manifest_digest": manifest["manifest_digest"],
            "coverage_manifest_events": manifest["event_count"],
            "events_seen": len(events),
            "before_window": before_window,
            "enqueued": 0,
            "skipped": len(prepared),
            "schedule_races": 0,
        }
    for item in prepared:
        row = item["row"]
        status = item["status"]
        cadence = int(item["cadence"])
        generation_at = str(item["generation_at"])
        if not item["dispatch_required"]:
            skipped += 1
            continue
        dispatched_event = {
            key: row.get(key)
            for key in (
                "event_key", "event_id", "sport_key", "sport_title", "commence_time",
                "home_team", "away_team", "schedule_revision", "schedule_identity",
            )
        }
        try:
            discovery = store.put_coverage_discovery_attempt(
                dispatched_event,
                discovery_observed_at=generation_at,
                status="QUEUED",
                observed_at=dispatch_observed_at,
            )
            if not discovery.get("latest_summary_updated"):
                skipped += 1
                continue
            store.enqueue(
                _job(
                    "DISCOVER_EVENT",
                    event=dispatched_event,
                    dispatch_observed_at=generation_at,
                    cadence_seconds=cadence,
                    collection_window=status,
                )
            )
        except Exception:
            try:
                store.put_coverage_discovery_attempt(
                    dispatched_event,
                    discovery_observed_at=generation_at,
                    status="ENQUEUE_FAILED",
                    observed_at=dispatch_observed_at,
                )
            except Exception:
                pass
            raise
        if item["recoverable_plan"]:
            # Recovery replays the already-dispatched generation.  Do not move
            # the event cadence anchor forward, or repeated recovery attempts
            # could postpone the next fresh generation indefinitely.
            recovered_plans += 1
        elif not store.mark_dispatched(
            row["event_key"],
            dispatch_observed_at,
            schedule_revision=int(row.get("schedule_revision") or 0),
            schedule_identity_value=str(
                row.get("schedule_identity") or schedule_identity(row)
            ),
        ):
            schedule_races += 1
        enqueued += 1
    return {
        "ok": True,
        "system": "soccer_auto",
        "match_day_timezone": DAY_TIMEZONE,
        "daily_lead_hours": COLLECTION_LEAD_HOURS,
        "daily_windows": {key: value.__dict__ for key, value in windows.items()},
        "events_seen": len(events),
        "observed_at": dispatch_observed_at,
        "coverage_manifest_digest": manifest["manifest_digest"],
        "coverage_manifest_events": manifest["event_count"],
        "before_window": before_window,
        "enqueued": enqueued,
        "skipped": skipped,
        "schedule_races": schedule_races,
        "recovered_plans": recovered_plans,
    }


def inventory_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    """Refresh free fixture metadata outside the paid-odds work queue.

    Schedule discovery is the authority for the daily T-10 boundary. Keeping it
    out of the market fan-out queue prevents a large global slate from starving
    kickoff revisions or newly posted leagues.
    """
    store = SoccerStore()
    observed = now_utc()
    generation_id = f"{iso_utc(observed)}#{uuid.uuid4().hex}"
    lease = store.begin_event_inventory_generation(
        generation_id=generation_id,
        observed_at=iso_utc(observed),
    )
    if not lease.get("acquired"):
        return {
            "ok": True,
            "system": "soccer_auto",
            "fixture_inventory_only": True,
            "queue_bypassed": True,
            "inventory_generation_deferred": True,
            "active_generation_id": str(lease.get("generation_id") or ""),
            "authority_state": str(lease.get("authority_state") or "MISSING"),
            "competitions_refreshed": 0,
            "skipped": 0,
            "failures": [],
        }
    refreshed = 0
    failures: list[dict[str, str]] = []
    try:
        client = _client()
        catalog = _catalog_snapshot(
            store,
            client,
            observed_at=iso_utc(observed),
            persist=True,
        )
        # The global inventory-generation lease is the single-flight boundary.
        # Per-sport slot claims would survive a hard crash longer than this
        # generation and could let a reclaimed run falsely skip part of the
        # authoritative universe.
        for competition in store.list_competitions(active_only=True):
            sport_key = str(competition["sport_key"])
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
                failures.append({"sport_key": sport_key, "error": str(exc)})
        finished = store.finish_event_inventory_generation(
            generation_id=generation_id,
            observed_at=iso_utc(now_utc()),
            success=not failures,
            competitions_refreshed=refreshed,
            failures=failures,
        )
        if not finished.get("updated"):
            failures.append(
                {
                    "sport_key": "*authority*",
                    "error": "EVENT_INVENTORY_GENERATION_FENCE_LOST",
                }
            )
    except Exception as exc:
        try:
            store.finish_event_inventory_generation(
                generation_id=generation_id,
                observed_at=iso_utc(now_utc()),
                success=False,
                competitions_refreshed=refreshed,
                failures=[*failures, {"sport_key": "*handler*", "error": str(exc)}],
            )
        except Exception:
            pass
        raise
    return {
        "ok": not failures,
        "system": "soccer_auto",
        "fixture_inventory_only": True,
        "queue_bypassed": True,
        "inventory_generation_id": generation_id,
        "authority_state": "COMPLETED" if not failures else "FAILED",
        "soccer_competitions": int(catalog["soccer_competitions"]),
        "catalog_digest": str(catalog["catalog_digest"]),
        "competitions_refreshed": refreshed,
        "skipped": 0,
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
    if not isinstance(events, list):
        raise RuntimeError("soccer events response must be a list")
    stored_count = 0
    seen_event_keys: list[str] = []
    for event in events:
        if not event.get("id") or not event.get("commence_time"):
            continue
        event = {**event, "sport_key": event.get("sport_key") or sport_key}
        stored = store.put_event(event, observed_at)
        seen_event_keys.append(str(stored["event_key"]))
        stored_count += 1
    omitted_count = store.record_event_inventory_omissions(
        sport_key,
        seen_event_keys=seen_event_keys,
        observed_at=observed_at,
    )
    return {
        "sport_key": sport_key,
        "events": len(events),
        "events_stored": stored_count,
        "events_omitted_after_success": omitted_count,
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


def _discover_event(
    store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]
) -> dict[str, Any]:
    event = dict(job["event"])
    execution_observed_at = _observed_at()
    try:
        _require_collection_window(store, event, execution_observed_at)
    except RuntimeError as exc:
        if "stale soccer collection job" not in str(exc):
            raise
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "STALE_EVENT_SCHEDULE",
            "fetch_jobs_enqueued": 0,
        }
    generation_at = str(
        job.get("dispatch_observed_at") or execution_observed_at
    )
    execution_token = uuid.uuid4().hex
    lease = store.begin_coverage_discovery_execution(
        event_key=str(event["event_key"]),
        discovery_observed_at=generation_at,
        schedule_revision=int(event.get("schedule_revision") or 0),
        execution_token=execution_token,
        observed_at=execution_observed_at,
    )
    if not lease.get("acquired"):
        if str(lease.get("state") or "") == "COMPLETED":
            return {
                "event_key": event["event_key"],
                "duplicate": True,
                "reason": "COVERAGE_DISCOVERY_ALREADY_COMPLETED",
                "provider_called": False,
                "fetch_jobs_enqueued": 0,
            }
        raise CoverageExecutionDeferred(
            {
                "event_key": event["event_key"],
                "deferred": True,
                "reason": "COVERAGE_DISCOVERY_LEASE_BUSY",
                "lease_expires_at": lease.get("lease_expires_at"),
            }
        )
    try:
        result = _discover_event_impl(
            store,
            client,
            {**job, "_execution_observed_at": execution_observed_at},
        )
        completed = store.complete_coverage_discovery_execution(
            event_key=str(event["event_key"]),
            discovery_observed_at=generation_at,
            schedule_revision=int(event.get("schedule_revision") or 0),
            execution_token=execution_token,
            observed_at=execution_observed_at,
        )
        if not completed:
            store.release_coverage_discovery_execution(
                event_key=str(event["event_key"]),
                discovery_observed_at=generation_at,
                schedule_revision=int(event.get("schedule_revision") or 0),
                execution_token=execution_token,
            )
            if not result.get("stale_cycle"):
                raise CoverageExecutionDeferred(
                    {
                        "event_key": event["event_key"],
                        "deferred": True,
                        "reason": "COVERAGE_DISCOVERY_EVIDENCE_PENDING",
                    }
                )
        return result
    except Exception:
        try:
            store.release_coverage_discovery_execution(
                event_key=str(event["event_key"]),
                discovery_observed_at=generation_at,
                schedule_revision=int(event.get("schedule_revision") or 0),
                execution_token=execution_token,
            )
        except Exception:
            pass
        raise


def _discover_event_impl(
    store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]
) -> dict[str, Any]:
    event = dict(job["event"])
    request_observed_at = str(
        job.get("_execution_observed_at") or _observed_at()
    )
    discovery_observed_at = str(
        job.get("dispatch_observed_at") or request_observed_at
    )
    try:
        window = _require_collection_window(store, event, request_observed_at)
    except RuntimeError as exc:
        if "stale soccer collection job" not in str(exc):
            raise
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "STALE_EVENT_SCHEDULE",
            "fetch_jobs_enqueued": 0,
        }
    discovery = store.put_coverage_discovery_attempt(
        event,
        discovery_observed_at=discovery_observed_at,
        status="STARTED",
        observed_at=request_observed_at,
    )
    if not discovery.get("latest_summary_updated"):
        reason = (
            "COVERAGE_DISCOVERY_ALREADY_COMPLETED"
            if str(discovery.get("discovery_status") or "") == "HTTP_200"
            else "NEWER_COVERAGE_DISCOVERY_ALREADY_EXISTS"
        )
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": reason,
            "fetch_jobs_enqueued": 0,
        }
    if (
        discovery.get("plan_observed_at")
        and discovery.get("plan_digest")
        and discovery.get("request_markets")
    ):
        fanout = _enqueue_coverage_fanout(
            store,
            event,
            discovery,
            observed_at=request_observed_at,
        )
        return {
            "event_key": event["event_key"],
            "resumed_existing_plan": True,
            **fanout,
            "collection_window": window,
        }
    inventory_by_book: dict[str, dict[str, Any]] = {}
    discovery_uris: list[str] = []
    # The event-market endpoint accepts comma-separated regions and costs one
    # credit per request. Query the complete current region catalog at once so
    # soccer receives the same bookmaker/market union without nine separate
    # calls competing with MLB and tennis for the shared subscription.
    admission = _provider_budget_admission(
        store,
        "event_markets",
        request_observed_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS,
    )
    if not admission["available"]:
        external_capacity = bool(admission["external_capacity"])
        budget_reason = str(admission["reason"])
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=discovery_observed_at,
            status=("QUOTA_DEFERRED" if external_capacity else "ADMISSION_DEFERRED"),
            observed_at=request_observed_at,
            budget_reason=budget_reason,
        )
        error_type = ProviderBudgetDeferred if external_capacity else CoverageExecutionDeferred
        raise error_type({
            "event_key": event["event_key"],
            "deferred": True,
            "reason": budget_reason,
            "external_capacity": external_capacity,
            "regions_completed": 0,
        })
    store.record_collection_window_call(event, window, request_observed_at)
    try:
        response = client.event_markets(
            event["sport_key"],
            event["event_id"],
            regions=ALL_BOOKMAKER_REGIONS,
        )
    except OddsApiError as exc:
        store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=discovery_observed_at,
            status="RETRYABLE_ERROR" if exc.retryable else "REQUEST_REJECTED",
            observed_at=request_observed_at,
        )
        store.record_collection_failure(
            event_key=event["event_key"],
            operation="event_markets",
            observed_at=request_observed_at,
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
    observed_at = _observed_at()
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

    current_inventory = {
        book: {
            "title": row["title"],
            "regions": sorted(row["regions"]),
            "markets": sorted(row["markets"]),
        }
        for book, row in sorted(inventory_by_book.items())
    }
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
    # Persist only what this discovery actually saw. Rewriting the rolling
    # union with a fresh timestamp would keep a delisted pair alive forever.
    if current_inventory:
        store.put_market_inventory(event["event_key"], current_inventory, observed_at)
    all_discovered = {
        market
        for row in inventory_by_book.values()
        for market in row["markets"]
    }
    all_markets = _market_keys_for_sport(event["sport_key"], all_discovered)
    game_market_scope = set(all_markets)
    game_inventory = {
        book: {
            **detail,
            "markets": sorted(set(detail.get("markets") or ()) & game_market_scope),
        }
        for book, detail in serializable_inventory.items()
        if set(detail.get("markets") or ()) & game_market_scope
    }
    current_game_inventory = {
        book: {
            **detail,
            "markets": sorted(set(detail.get("markets") or ()) & game_market_scope),
        }
        for book, detail in current_inventory.items()
        if set(detail.get("markets") or ()) & game_market_scope
    }
    plan = store.put_coverage_plan(
        event["event_key"],
        game_inventory,
        observed_at,
        required_inventory=current_game_inventory,
        event=event,
        discovery_observed_at=discovery_observed_at,
        request_markets=all_markets,
    )
    if plan.get("coverage_error"):
        raise RuntimeError(
            "Soccer coverage plan failed closed: "
            f"{plan.get('coverage_error')} ({plan.get('coverage_item_size_bytes')})"
        )
    plan_matches_generation = bool(
        str(plan.get("discovery_observed_at") or "") == discovery_observed_at
        and str(plan.get("plan_version") or "") == COVERAGE_PLAN_VERSION
        and int(plan.get("schedule_revision") or 0)
        == int(event.get("schedule_revision") or 0)
        and str(plan.get("schedule_identity") or "")
        == str(event.get("schedule_identity") or schedule_identity(event))
        and plan.get("plan_observed_at")
        and plan.get("plan_digest")
    )
    if not plan_matches_generation:
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "NEWER_COVERAGE_PLAN_ALREADY_EXISTS",
            "fetch_jobs_enqueued": 0,
        }
    books = sorted(inventory_by_book)
    fanout = _enqueue_coverage_fanout(
        store,
        event,
        plan,
        observed_at=observed_at,
    )
    return {
        "event_key": event["event_key"],
        "bookmakers": len(books),
        "discovered_markets": len(all_discovered),
        "market_scope": len(plan.get("request_markets") or ()),
        **fanout,
        "discovery_uris": discovery_uris,
        "collection_window": window,
    }


def _fetch_event(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(job.get("event") or {})
    plan_digest = str(job.get("plan_digest") or "")
    batch_digest = str(job.get("batch_digest") or "")
    plan_observed_at = str(job.get("discovery_observed_at") or "")
    if (
        not event.get("event_key")
        or not plan_digest
        or not batch_digest
        or not plan_observed_at
        or str(job.get("batch_digest")) != _coverage_batch_digest(job)
    ):
        return _fetch_event_impl(store, client, job)
    if not store.coverage_plan_is_current(
        event["event_key"],
        plan_observed_at=plan_observed_at,
        plan_digest=plan_digest,
    ):
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "STALE_COVERAGE_PLAN",
            "provider_called": False,
        }
    lease_observed_at = _observed_at()
    execution_token = uuid.uuid4().hex
    lease = store.begin_coverage_fetch_execution(
        event_key=str(event["event_key"]),
        plan_digest=plan_digest,
        batch_digest=batch_digest,
        execution_token=execution_token,
        observed_at=lease_observed_at,
    )
    if not lease.get("acquired"):
        if str(lease.get("state") or "") == "COMPLETED":
            return {
                "event_key": event["event_key"],
                "duplicate": True,
                "reason": "COVERAGE_BATCH_ALREADY_COMPLETED",
                "provider_called": False,
            }
        raise CoverageExecutionDeferred(
            {
                "event_key": event["event_key"],
                "deferred": True,
                "reason": "COVERAGE_BATCH_LEASE_BUSY",
                "lease_expires_at": lease.get("lease_expires_at"),
            }
        )
    try:
        result = _fetch_event_impl(
            store,
            client,
            {**job, "_execution_observed_at": lease_observed_at},
        )
        completed = store.complete_coverage_fetch_execution(
            event_key=str(event["event_key"]),
            plan_digest=plan_digest,
            batch_digest=batch_digest,
            execution_token=execution_token,
            observed_at=lease_observed_at,
        )
        if not completed:
            # Stale/closed work has no terminal batch evidence and is safe to
            # acknowledge, but must not leave a lease that suppresses a valid
            # later delivery.
            store.release_coverage_fetch_execution(
                event_key=str(event["event_key"]),
                plan_digest=plan_digest,
                batch_digest=batch_digest,
                execution_token=execution_token,
            )
            if str(result.get("reason") or "") not in {
                "STALE_COVERAGE_PLAN",
                "STALE_EVENT_SCHEDULE",
                "INVALID_COVERAGE_BATCH_PROVENANCE",
            }:
                raise CoverageExecutionDeferred(
                    {
                        "event_key": event["event_key"],
                        "deferred": True,
                        "reason": "COVERAGE_BATCH_EVIDENCE_PENDING",
                    }
                )
        return result
    except Exception:
        try:
            store.release_coverage_fetch_execution(
                event_key=str(event["event_key"]),
                plan_digest=plan_digest,
                batch_digest=batch_digest,
                execution_token=execution_token,
            )
        except Exception:
            # Do not mask the provider/merge exception. The short lease is
            # reclaimable, and terminal evidence is checked before re-entry.
            pass
        raise


def _fetch_event_impl(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(job["event"])
    bookmakers = tuple(job.get("bookmakers") or ())
    regions = tuple(job.get("regions") or ALL_BOOKMAKER_REGIONS)
    markets = tuple(job["markets"])
    planned_pairs = tuple(str(pair) for pair in job.get("planned_pairs") or ())
    plan_observed_at = str(job.get("discovery_observed_at") or "") or None
    plan_digest = str(job.get("plan_digest") or "") or None
    request_observed_at = str(
        job.get("_execution_observed_at") or _observed_at()
    )
    if (
        not plan_observed_at
        or not plan_digest
        or "planned_pairs" not in job
        or not job.get("batch_digest")
        or str(job.get("batch_digest")) != _coverage_batch_digest(job)
        or (
            job.get("split_leaf_id")
            and str(job.get("split_leaf_id"))
            != _coverage_leaf_scope_digest(job)
        )
    ):
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "INVALID_COVERAGE_BATCH_PROVENANCE",
            "provider_called": False,
        }
    coverage_split = {
        "batch_digest": str(job.get("batch_digest") or "") or None,
        "split_group_digest": str(job.get("split_group_digest") or "") or None,
        "attempted_regions": regions,
        "split_expected_regions": tuple(job.get("split_expected_regions") or ()),
        "split_leaf_id": str(job.get("split_leaf_id") or "") or None,
        "split_expected_leaf_ids": tuple(
            job.get("split_expected_leaf_ids") or ()
        ),
    }
    if not store.coverage_plan_is_current(
            event["event_key"],
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
        ):
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "STALE_COVERAGE_PLAN",
            "provider_called": False,
        }
    try:
        window = _require_collection_window(store, event, request_observed_at)
    except RuntimeError as exc:
        if "stale soccer collection job" not in str(exc):
            raise
        return {
            "event_key": event["event_key"],
            "stale_cycle": True,
            "reason": "STALE_EVENT_SCHEDULE",
            "provider_called": False,
        }
    region_equivalents = max(1, (len(bookmakers) + 9) // 10) if bookmakers else max(1, len(regions))
    admission = _provider_budget_admission(
        store,
        "event_odds",
        request_observed_at,
        estimated_cost=(
            DEFAULT_MAX_ATTEMPTS * max(1, len(markets)) * region_equivalents
        ),
    )
    if not admission["available"]:
        external_capacity = bool(admission["external_capacity"])
        budget_reason = str(admission["reason"])
        result = {
            "event_key": event["event_key"],
            "deferred": True,
            "reason": budget_reason,
            "external_capacity": external_capacity,
            "planned_pairs": len(planned_pairs),
        }
        attempt = store.put_coverage_fetch(
            event["event_key"],
            {"bookmakers": []},
            observed_at=request_observed_at,
            requested_bookmakers=bookmakers,
            requested_markets=markets,
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
            planned_pairs=planned_pairs,
            raw_returned_pairs=(),
            outcome=("QUOTA_DEFERRED" if external_capacity else "RETRYABLE_ERROR"),
            budget_reason=budget_reason,
            absence_scope_complete=False,
            **coverage_split,
        )
        if plan_observed_at and plan_digest and not attempt.get("latest_summary_updated"):
            if store.coverage_plan_is_current(
                event["event_key"],
                plan_observed_at=plan_observed_at,
                plan_digest=plan_digest,
            ):
                raise CoverageExecutionDeferred(
                    {
                        **result,
                        "reason": "COVERAGE_SPLIT_FRONTIER_PENDING",
                        "budget_reason": budget_reason,
                    }
                )
            return {
                "event_key": event["event_key"],
                "stale_cycle": True,
                "reason": "STALE_COVERAGE_PLAN",
                "provider_called": False,
            }
        error_type = ProviderBudgetDeferred if external_capacity else CoverageExecutionDeferred
        raise error_type(result)
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
            store.put_coverage_fetch(
                event["event_key"],
                {"bookmakers": []},
                observed_at=request_observed_at,
                requested_bookmakers=bookmakers,
                requested_markets=markets,
                plan_observed_at=plan_observed_at,
                plan_digest=plan_digest,
                planned_pairs=planned_pairs,
                raw_returned_pairs=(),
                outcome="RETRYABLE_ERROR",
                absence_scope_complete=False,
                **coverage_split,
            )
            raise
        child_jobs: list[dict[str, Any]] = []
        split_reason = ""
        if len(markets) > 1:
            midpoint = max(1, len(markets) // 2)
            child_jobs = [
                _fetch_child_job(job, markets=subset)
                for subset in (markets[:midpoint], markets[midpoint:])
                if subset
            ]
            split_reason = "NONRETRYABLE_MARKET_BATCH_ERROR"
        elif len(bookmakers) > 1:
            midpoint = max(1, len(bookmakers) // 2)
            child_jobs = [
                _fetch_child_job(job, bookmakers=subset)
                for subset in (bookmakers[:midpoint], bookmakers[midpoint:])
                if subset
            ]
            split_reason = "NONRETRYABLE_BOOKMAKER_BATCH_ERROR"
        elif not bookmakers and len(regions) > 1:
            midpoint = max(1, len(regions) // 2)
            child_jobs = [
                _fetch_child_job(job, regions=subset)
                for subset in (regions[:midpoint], regions[midpoint:])
                if subset
            ]
            split_reason = "NONRETRYABLE_REGION_BATCH_ERROR"
        if child_jobs:
            child_jobs = _bind_split_children(job, child_jobs)
            # Send every deterministic child before recording the parent as
            # handled. A crash can resend children, but their execution leases
            # make that safe; it cannot durably omit a child.
            for child in child_jobs:
                store.enqueue(child)
            split_evidence = {
                **coverage_split,
                "split_group_digest": str(
                    child_jobs[0]["split_group_digest"]
                ),
                "split_expected_leaf_ids": tuple(
                    child_jobs[0]["split_expected_leaf_ids"]
                ),
            }
            store.put_coverage_fetch(
                event["event_key"],
                {"bookmakers": []},
                observed_at=request_observed_at,
                requested_bookmakers=bookmakers,
                requested_markets=markets,
                plan_observed_at=plan_observed_at,
                plan_digest=plan_digest,
                planned_pairs=planned_pairs,
                raw_returned_pairs=(),
                outcome="SPLIT_PENDING",
                absence_scope_complete=False,
                split_child_leaf_ids=tuple(
                    child["split_leaf_id"] for child in child_jobs
                ),
                **split_evidence,
            )
            return {
                "event_key": event["event_key"],
                "split": True,
                "reason": split_reason,
                "child_jobs": len(child_jobs),
            }
        store.put_coverage_fetch(
            event["event_key"],
            {"bookmakers": []},
            observed_at=request_observed_at,
            requested_bookmakers=bookmakers,
            requested_markets=markets,
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
            planned_pairs=planned_pairs,
            raw_returned_pairs=(),
            outcome="REQUEST_REJECTED",
            absence_scope_complete=False,
            **coverage_split,
        )
        store.record_collection_failure(
            event_key=event["event_key"],
            operation="event_odds",
            observed_at=request_observed_at,
            detail=str(exc),
            scope={
                "bookmakers": list(bookmakers),
                "regions": list(regions),
                "markets": list(markets),
            },
            permanent=True,
        )
        return {
            "event_key": event["event_key"],
            "quarantined": True,
            "reason": "UNSUPPORTED_BOOKMAKER_MARKET_BATCH_SCOPE",
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
    raw_returned_pairs = _payload_pair_keys(response.data or {})
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
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
            planned_pairs=planned_pairs,
            raw_returned_pairs=raw_returned_pairs,
            outcome="RESPONSE_INVALID",
            absence_scope_complete=False,
            **coverage_split,
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
        plan_observed_at=plan_observed_at,
        plan_digest=plan_digest,
        planned_pairs=planned_pairs,
        raw_returned_pairs=raw_returned_pairs,
        outcome="HTTP_200",
        absence_scope_complete=(
            bool(bookmakers)
            or set(regions) == set(ALL_BOOKMAKER_REGIONS)
        ),
        **coverage_split,
    )
    result["returned_bookmakers"] = len(normalized.get("bookmakers") or [])
    return result


def _fetch_outrights(store: SoccerStore, client: OddsApiClient, job: Mapping[str, Any]) -> dict[str, Any]:
    sport_key = str(job["sport_key"])
    observed_at = _observed_at()
    # Outrights are tournament-level products without a game-day kickoff and
    # are archived separately; they never enter game prediction training.
    admission = _provider_budget_admission(
        store,
        "outrights",
        observed_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS * len(ALL_BOOKMAKER_REGIONS),
    )
    if not admission["available"]:
        result = {
            "sport_key": sport_key,
            "deferred": True,
            "reason": str(admission["reason"]),
            "external_capacity": bool(admission["external_capacity"]),
        }
        error_type = (
            ProviderBudgetDeferred
            if admission["external_capacity"]
            else CoverageExecutionDeferred
        )
        raise error_type(result)
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
        # Schedule mutations are fenced by inventory_handler's global
        # generation lease. Retire any legacy/manual queue payload normally so
        # it cannot bypass that fence or poison the DLQ.
        return {
            "system": "soccer_auto",
            "skipped": True,
            "reason": "EVENT_INVENTORY_HANDLER_ONLY",
            "sport_key": str(job.get("sport_key") or ""),
        }
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
            except (ProviderBudgetDeferred, CoverageExecutionDeferred) as exc:
                receive_count = max(
                    1,
                    int((record.get("attributes") or {}).get("ApproximateReceiveCount") or 1),
                )
                deferral_count = max(
                    receive_count,
                    int(job.get("quota_deferral_count") or 0) + 1,
                )
                visibility_seconds = min(
                    900,
                    30 * (2 ** min(deferral_count - 1, 5)),
                )
                # Outrights have no match kickoff at which a queued message can
                # retire. Their five-minute scheduler is the retry authority.
                if job.get("action") == "FETCH_OUTRIGHTS":
                    processed.append(
                        {
                            **exc.result,
                            "retry_via_scheduler": True,
                            "receive_count": receive_count,
                        }
                    )
                    continue
                replacement_enqueued = False
                try:
                    store.enqueue(
                        {
                            **job,
                            "quota_deferral_count": deferral_count,
                            "quota_deferred_at": _observed_at(),
                        },
                        delay_seconds=visibility_seconds,
                    )
                    replacement_enqueued = True
                except Exception:
                    # The replacement must exist before this delivery is
                    # acknowledged. If send fails, Lambda/SQS retries the
                    # original under the normal poison-message policy.
                    failures.append({"itemIdentifier": record.get("messageId")})
                processed.append(
                    {
                        **exc.result,
                        "retry_reenqueued": replacement_enqueued,
                        "retry_visible_in_seconds": visibility_seconds,
                        "receive_count": receive_count,
                        "quota_deferral_count": deferral_count,
                    }
                )
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
