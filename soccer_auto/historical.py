"""Resumable, bounded raw historical odds backfill.

Historical odds are archived but never labeled from prices.  The Odds API does
not return historical final results, so these rows stay outside supervised
training until an authoritative result is present.

Every competition owns an independent cursor.  This is important because the
provider catalog is dynamic: a newly inserted sport key must never inherit the
timestamp that previously belonged to a numeric list position.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping, Sequence

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .canonical import digest, iso_utc, parse_utc
from .collector import _client, _market_keys_for_sport
from .config import (
    ALL_BOOKMAKER_REGIONS,
    FEATURED_GAME_MARKETS,
    HISTORICAL_ADDITIONAL_START,
    HISTORICAL_FEATURED_START,
    HISTORICAL_FEATURED_START_BY_SPORT,
    SOCCER_MARKET_SEEDS,
)
from .odds_api import DEFAULT_MAX_ATTEMPTS, OddsApiError, chunks
from .storage import SoccerStore, ddb_safe, now_utc, plain


FEATURED_CALLS_PER_CYCLE = max(
    1, int(os.getenv("SOCCER_AUTO_HISTORICAL_FEATURED_CALLS_PER_CYCLE", "3"))
)
ADDITIONAL_EVENTS_PER_CYCLE = max(
    1, int(os.getenv("SOCCER_AUTO_HISTORICAL_ADDITIONAL_EVENTS_PER_CYCLE", "2"))
)
MARKETS_PER_REQUEST = max(
    1, int(os.getenv("SOCCER_AUTO_HISTORICAL_MARKETS_PER_REQUEST", "1"))
)
# An event payload may request fewer calls (deployment smoke uses one), but it
# can never raise this environment-owned ceiling.
MAX_CALLS_PER_INVOCATION = min(
    100,
    max(1, int(os.getenv("SOCCER_AUTO_HISTORICAL_MAX_CALLS_PER_INVOCATION", "25"))),
)

ACTIVE_CURSOR_STATES = frozenset(
    {"PENDING", "RUNNING", "QUOTA_DEFERRED", "ERROR_RETRYABLE"}
)


class HistoricalCursorConflict(RuntimeError):
    """A concurrent invocation advanced a cursor before this writer."""


class HistoricalTimestampError(ValueError):
    """Provider chronology cannot safely advance an immutable cursor."""


class HistoricalManifestConflict(RuntimeError):
    """One natural historical snapshot identity returned different bytes."""


class HistoricalWrapperSchemaError(ValueError):
    """An HTTP-200 historical response did not match the provider contract."""

    def __init__(self, detail: str, *, payload: Any = None):
        top_level_keys: list[str] = []
        if isinstance(payload, Mapping):
            for index, key in enumerate(payload):
                if index >= 12:
                    top_level_keys.append("<truncated>")
                    break
                top_level_keys.append(str(key).replace("\n", " ")[:48])

        def bounded_scalar(value: Any, limit: int) -> str:
            if value is None:
                return ""
            if not isinstance(value, (str, int, float, bool)):
                return f"<{type(value).__name__}>"
            return " ".join(str(value).split())[:limit]

        self.top_level_keys = tuple(top_level_keys)
        self.error_code = bounded_scalar(
            payload.get("error_code") if isinstance(payload, Mapping) else None,
            80,
        )
        self.provider_message = bounded_scalar(
            payload.get("message") if isinstance(payload, Mapping) else None,
            256,
        )
        super().__init__(
            f"{detail}; top_level_keys={list(self.top_level_keys)!r}; "
            f"error_code={self.error_code!r}; message={self.provider_message!r}"
        )


def _enabled() -> bool:
    return os.getenv("SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED", "false").strip().lower() == "true"


def _cursor_sk(name: str, sport_key: str) -> str:
    return f"{name.upper()}#{sport_key}"


def _cursor(store: SoccerStore, name: str, sport_key: str, start: str) -> dict[str, Any]:
    row = store.ops.get_item(
        Key={"PK": "HISTORICAL_CURSOR", "SK": _cursor_sk(name, sport_key)},
        ConsistentRead=True,
    ).get("Item")
    if row:
        cursor = plain(row)
        cursor["_persisted"] = True
        cursor["revision"] = int(cursor.get("revision") or 0)
        return cursor
    return {
        "PK": "HISTORICAL_CURSOR",
        "SK": _cursor_sk(name, sport_key),
        "entity_type": "SOCCER_HISTORICAL_BACKFILL_CURSOR",
        "mode": name.upper(),
        "sport_key": sport_key,
        "snapshot_at": iso_utc(start),
        "status": "PENDING",
        "calls_completed": 0,
        "revision": 0,
        "_persisted": False,
    }


def _conditional_failure(exc: BaseException) -> bool:
    return (
        isinstance(exc, ClientError)
        and (exc.response.get("Error") or {}).get("Code")
        == "ConditionalCheckFailedException"
    )


def _save_cursor(store: SoccerStore, cursor: dict[str, Any]) -> None:
    """Advance one cursor with compare-and-swap semantics.

    Provider calls can still overlap after a manual invocation, but an older
    writer can never move a durable cursor backward.  Identical raw payloads
    remain auditable in versioned S3.
    """
    persisted = bool(cursor.pop("_persisted", True))
    expected_revision = int(cursor.get("revision") or 0)
    item = dict(cursor)
    item["revision"] = expected_revision + 1
    if not persisted:
        condition = "attribute_not_exists(SK)"
        values = None
    elif expected_revision == 0:
        # Migrate a pre-revision cursor without replacing a newer writer.
        condition = "attribute_not_exists(revision)"
        values = None
    else:
        condition = "revision=:expected"
        values = {":expected": expected_revision}
    kwargs: dict[str, Any] = {
        "Item": ddb_safe(item),
        "ConditionExpression": condition,
    }
    if values:
        kwargs["ExpressionAttributeValues"] = values
    try:
        store.ops.put_item(**kwargs)
    except ClientError as exc:
        cursor["_persisted"] = persisted
        if _conditional_failure(exc):
            raise HistoricalCursorConflict(
                f"historical cursor changed concurrently: {cursor.get('SK')}"
            ) from exc
        raise
    cursor.clear()
    cursor.update(plain(item))
    cursor["_persisted"] = True


def _competitions(store: SoccerStore) -> list[dict[str, Any]]:
    return sorted(
        [
            row
            for row in store.list_competitions()
            if not row.get("has_outrights") and row.get("sport_key")
        ],
        key=lambda row: str(row["sport_key"]),
    )


def _cursor_start(
    name: str,
    sport_key: str,
    fallback_start: str,
) -> tuple[str, bool]:
    """Return a mode-safe cursor start and whether it is provider-published.

    Featured history begins at each sport's own first snapshot. Additional
    markets cannot precede either that snapshot or the provider-wide
    additional-market launch. Unknown future sport keys retain the global
    fallback and are not eligible for automatic quarantine migration.
    """
    featured_start = HISTORICAL_FEATURED_START_BY_SPORT.get(sport_key)
    if featured_start is None:
        return iso_utc(fallback_start), False
    if name.upper() == "FEATURED":
        return iso_utc(featured_start), True
    if name.upper() == "ADDITIONAL":
        additional_start = max(
            parse_utc(fallback_start),
            parse_utc(featured_start),
        )
        return iso_utc(additional_start), True
    return iso_utc(fallback_start), False


def _migrate_prestart_schema_quarantine(
    store: SoccerStore,
    cursor: dict[str, Any],
    *,
    expected_mode: str,
    expected_sport_key: str,
    official_start: str,
) -> None:
    """Repair only the known pre-coverage, zero-progress quarantine case."""
    expected_mode = expected_mode.upper()
    try:
        eligible = bool(
            cursor.get("_persisted") is True
            and cursor.get("entity_type") == "SOCCER_HISTORICAL_BACKFILL_CURSOR"
            and str(cursor.get("status") or "") == "QUARANTINED_PROVIDER_SCHEMA"
            and int(cursor.get("calls_completed") or 0) == 0
            and expected_mode in {"FEATURED", "ADDITIONAL"}
            and str(cursor.get("mode") or "") == expected_mode
            and str(cursor.get("sport_key") or "") == expected_sport_key
            and str(cursor.get("SK") or "")
            == _cursor_sk(expected_mode, expected_sport_key)
            and expected_sport_key in HISTORICAL_FEATURED_START_BY_SPORT
            and parse_utc(str(cursor["snapshot_at"])) < parse_utc(official_start)
        )
    except (KeyError, TypeError, ValueError):
        eligible = False
    if not eligible:
        return

    previous_snapshot_at = iso_utc(str(cursor["snapshot_at"]))
    migrated_at = iso_utc(now_utc())
    cursor["snapshot_at"] = iso_utc(official_start)
    cursor["status"] = "PENDING"
    cursor["updated_at"] = migrated_at
    cursor["prestart_schema_quarantine_migrated_at"] = migrated_at
    cursor["prestart_schema_quarantine_from"] = previous_snapshot_at
    cursor["official_historical_start"] = iso_utc(official_start)
    cursor["recovery_reason"] = "OFFICIAL_SPORT_START_CORRECTION"
    for key in (
        "pending_events",
        "pending_event_index",
        "pending_market_index",
        "pending_sport_key",
        "pending_provider_at",
        "pending_requested_at",
        "pending_next_timestamp",
        "pending_market_plan",
        "pending_market_plan_digest",
        "last_provider_at",
        "last_progress_at",
        "last_error",
        "last_error_at",
        "quota_deferred_at",
    ):
        cursor.pop(key, None)
    if expected_mode == "ADDITIONAL":
        cursor["pending_events"] = []
        cursor["pending_event_index"] = 0
        cursor["pending_market_index"] = 0
    _save_cursor(store, cursor)


def _cursor_rows(
    store: SoccerStore,
    name: str,
    start: str,
    competitions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    cursors: list[dict[str, Any]] = []
    for competition in competitions:
        sport_key = str(competition["sport_key"])
        cursor_start, provider_published = _cursor_start(name, sport_key, start)
        cursor = _cursor(store, name, sport_key, cursor_start)
        if provider_published:
            _migrate_prestart_schema_quarantine(
                store,
                cursor,
                expected_mode=name,
                expected_sport_key=sport_key,
                official_start=cursor_start,
            )
        cursors.append(cursor)
    return cursors


def _next_active_cursor(cursors: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    active = [
        cursor
        for cursor in cursors
        if str(cursor.get("status") or "PENDING") in ACTIVE_CURSOR_STATES
    ]
    if not active:
        return None
    return min(
        active,
        key=lambda cursor: (
            parse_utc(str(cursor["snapshot_at"])),
            str(cursor["sport_key"]),
        ),
    )


def _provider_timestamps(wrapper: Mapping[str, Any], requested: str) -> tuple[str, str]:
    """Return normalized provider and next timestamps with strict progress."""
    requested_at = iso_utc(requested)
    requested_dt = parse_utc(requested_at)
    provider_at = iso_utc(str(wrapper.get("timestamp") or requested_at))
    provider_dt = parse_utc(provider_at)
    if provider_dt > requested_dt:
        raise HistoricalTimestampError(
            f"provider timestamp {provider_at} is later than requested {requested_at}"
        )
    if wrapper.get("next_timestamp"):
        next_at = iso_utc(str(wrapper["next_timestamp"]))
    else:
        interval = timedelta(
            minutes=10 if provider_dt < parse_utc("2022-09-18T00:00:00Z") else 5
        )
        # Advance from the requested checkpoint, not an unexpectedly old
        # provider timestamp, so a sparse response cannot rewind the cursor.
        next_at = iso_utc(requested_dt + interval)
    if parse_utc(next_at) <= requested_dt:
        raise HistoricalTimestampError(
            f"historical next timestamp {next_at} did not advance {requested_at}"
        )
    return provider_at, next_at


def _next_timestamp(wrapper: Mapping[str, Any], requested: str) -> str:
    """Compatibility wrapper retained for focused cursor tests."""
    return _provider_timestamps(wrapper, requested)[1]


def _validated_wrapper(payload: Any, *, operation: str) -> Mapping[str, Any]:
    """Validate the versioned historical envelope before any cursor progress.

    A successful HTTP status is not evidence that the JSON body has the
    historical snapshot shape. Advancing on ``{}``, an error object, or a
    partially decoded payload would create a permanent hole in the raw archive.
    """
    if not isinstance(payload, Mapping):
        raise HistoricalWrapperSchemaError(
            f"{operation} HTTP-200 payload must be a JSON object",
            payload=payload,
        )
    if "timestamp" not in payload or not payload.get("timestamp"):
        raise HistoricalWrapperSchemaError(
            f"{operation} HTTP-200 wrapper is missing timestamp",
            payload=payload,
        )
    if "data" not in payload:
        raise HistoricalWrapperSchemaError(
            f"{operation} HTTP-200 wrapper is missing data",
            payload=payload,
        )
    try:
        iso_utc(str(payload["timestamp"]))
        for key in ("previous_timestamp", "next_timestamp"):
            if payload.get(key):
                iso_utc(str(payload[key]))
    except (TypeError, ValueError) as exc:
        raise HistoricalWrapperSchemaError(
            f"{operation} HTTP-200 wrapper has an invalid snapshot timestamp",
            payload=payload,
        ) from exc

    data = payload["data"]
    event_rows: list[Mapping[str, Any]]
    if operation in {"historical_featured", "historical_events"}:
        if not isinstance(data, list):
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper data must be an event list",
                payload=payload,
            )
        if any(not isinstance(row, Mapping) for row in data):
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper contains a non-object event",
                payload=payload,
            )
        event_rows = list(data)
    elif operation == "historical_event_odds":
        if not isinstance(data, Mapping):
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper data must be one event object",
                payload=payload,
            )
        event_rows = [data]
    else:
        raise ValueError(f"unsupported historical wrapper operation: {operation}")
    required = ("id", "sport_key", "commence_time", "home_team", "away_team")
    for row in event_rows:
        if any(not str(row.get(key) or "").strip() for key in required):
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper contains an incomplete event identity",
                payload=payload,
            )
        try:
            iso_utc(str(row["commence_time"]))
        except (TypeError, ValueError) as exc:
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper contains an invalid event kickoff",
                payload=payload,
            ) from exc
        if operation != "historical_events" and not isinstance(
            row.get("bookmakers"), list
        ):
            raise HistoricalWrapperSchemaError(
                f"{operation} HTTP-200 wrapper event is missing bookmakers",
                payload=payload,
            )
    return payload


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
        }
    )
    key = {
        "PK": f"HISTORICAL_MANIFEST#{sport_key}",
        "SK": f"{provider_at}#{mode}#{identity}",
    }
    item = {
        **key,
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
    try:
        store.ops.put_item(
            Item=ddb_safe(item),
            ConditionExpression="attribute_not_exists(SK)",
        )
        return
    except ClientError as exc:
        if not _conditional_failure(exc):
            raise
    existing = plain(
        store.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
    )
    if existing.get("payload_sha256") == payload_hash:
        return
    observed_at = iso_utc(now_utc())
    store.ops.put_item(
        Item=ddb_safe(
            {
                "PK": "HISTORICAL_CONFLICT",
                "SK": f"{sport_key}#{provider_at}#{mode}#{identity}#{observed_at}",
                "entity_type": "SOCCER_HISTORICAL_RAW_CONFLICT",
                "sport_key": sport_key,
                "provider_at": provider_at,
                "mode": mode,
                "existing_payload_sha256": existing.get("payload_sha256"),
                "candidate_payload_sha256": payload_hash,
                "candidate_raw_uri": raw_uri,
                "observed_at": observed_at,
                "training_blocked": True,
            }
        )
    )
    raise HistoricalManifestConflict(
        f"historical provider repaint detected for {sport_key} {provider_at} {mode}"
    )


def _mark_complete(store: SoccerStore, cursor: dict[str, Any], observed_at: str) -> None:
    cursor["snapshot_at"] = iso_utc(str(cursor["snapshot_at"]))
    cursor["status"] = "COMPLETE"
    cursor["completed_at"] = observed_at
    cursor["updated_at"] = observed_at
    cursor.pop("quota_deferred_at", None)
    cursor.pop("last_error", None)
    cursor.pop("last_error_at", None)
    _save_cursor(store, cursor)


def _mark_timestamp_error(
    store: SoccerStore,
    cursor: dict[str, Any],
    observed_at: str,
    exc: HistoricalTimestampError,
) -> None:
    cursor["status"] = "QUARANTINED_TIMESTAMP"
    cursor["last_error"] = str(exc)[:1000]
    cursor["last_error_at"] = observed_at
    cursor["updated_at"] = observed_at
    _save_cursor(store, cursor)


def _mark_schema_error(
    store: SoccerStore,
    cursor: dict[str, Any],
    observed_at: str,
    exc: HistoricalWrapperSchemaError,
) -> None:
    cursor["status"] = "QUARANTINED_PROVIDER_SCHEMA"
    cursor["last_error"] = str(exc)[:1000]
    cursor["last_error_at"] = observed_at
    cursor["updated_at"] = observed_at
    _save_cursor(store, cursor)


def _quarantine_provider_scope(
    store: SoccerStore,
    cursor: dict[str, Any],
    *,
    operation: str,
    observed_at: str,
    exc: OddsApiError,
    scope: Mapping[str, Any] | None = None,
) -> None:
    cursor["status"] = "QUARANTINED_PROVIDER_SCOPE"
    cursor["last_error"] = str(exc)[:1000]
    cursor["last_error_at"] = observed_at
    cursor["updated_at"] = observed_at
    _save_cursor(store, cursor)
    store.ops.put_item(
        Item=ddb_safe(
            {
                "PK": f"HISTORICAL_SCOPE_FAILURE#{cursor['sport_key']}",
                "SK": f"{observed_at}#{operation}#{digest(scope or {})[:16]}",
                "entity_type": "SOCCER_HISTORICAL_SCOPE_FAILURE",
                "sport_key": cursor["sport_key"],
                "mode": cursor["mode"],
                "operation": operation,
                "scope": dict(scope or {}),
                "detail": str(exc)[:2000],
                "status_code": exc.status_code,
                "observed_at": observed_at,
                "training_eligible": False,
            }
        )
    )


def run_featured(
    store: SoccerStore,
    *,
    max_calls: int | None = None,
    sport_key: str | None = None,
) -> dict[str, Any]:
    competitions = _competitions(store)
    if sport_key:
        competitions = [row for row in competitions if row["sport_key"] == sport_key]
    cursors = _cursor_rows(store, "FEATURED", HISTORICAL_FEATURED_START, competitions)
    call_limit = min(
        FEATURED_CALLS_PER_CYCLE,
        MAX_CALLS_PER_INVOCATION,
        MAX_CALLS_PER_INVOCATION if max_calls is None else max(0, int(max_calls)),
    )
    if not competitions:
        return {"mode": "FEATURED", "calls": 0, "reason": "NO_COMPETITIONS"}
    if call_limit == 0:
        return {"mode": "FEATURED", "calls": 0, "reason": "CALL_LIMIT_ZERO"}
    client = _client()
    completed = 0
    archived: list[str] = []
    completed_this_cycle = 0
    attempted_sports: set[str] = set()
    # Completion transitions do not consume provider calls. Bound them by the
    # number of known cursors so all-finished state cannot loop forever.
    transition_budget = len(cursors) + call_limit
    while completed < call_limit and transition_budget > 0:
        transition_budget -= 1
        cursor = _next_active_cursor(
            [
                row
                for row in cursors
                if str(row.get("sport_key") or "") not in attempted_sports
            ]
        )
        if cursor is None:
            break
        attempted_sports.add(str(cursor["sport_key"]))
        requested_at = iso_utc(str(cursor["snapshot_at"]))
        cursor["snapshot_at"] = requested_at
        request_started_at = iso_utc(now_utc())
        if parse_utc(requested_at) >= now_utc() - timedelta(minutes=5):
            _mark_complete(store, cursor, request_started_at)
            completed_this_cycle += 1
            continue
        if not store.provider_budget_available(
            "historical_featured",
            request_started_at,
            estimated_cost=(
                DEFAULT_MAX_ATTEMPTS
                * 10
                * len(FEATURED_GAME_MARKETS)
                * len(ALL_BOOKMAKER_REGIONS)
            ),
        ):
            cursor["status"] = "QUOTA_DEFERRED"
            cursor["quota_deferred_at"] = request_started_at
            cursor["last_attempt_at"] = request_started_at
            cursor["updated_at"] = request_started_at
            _save_cursor(store, cursor)
            break
        try:
            response = client.historical_odds(
                str(cursor["sport_key"]),
                requested_at,
                FEATURED_GAME_MARKETS,
                regions=ALL_BOOKMAKER_REGIONS,
            )
        except OddsApiError as exc:
            if exc.retryable:
                raise
            failed_at = iso_utc(now_utc())
            _quarantine_provider_scope(
                store,
                cursor,
                operation="historical_featured",
                observed_at=failed_at,
                exc=exc,
                scope={"requested_at": requested_at, "markets": list(FEATURED_GAME_MARKETS)},
            )
            completed += 1
            continue
        response_observed_at = iso_utc(now_utc())
        store.record_quota(
            response,
            operation="historical_featured",
            observed_at=response_observed_at,
        )
        try:
            wrapper = _validated_wrapper(
                response.data,
                operation="historical_featured",
            )
        except HistoricalWrapperSchemaError as exc:
            _mark_schema_error(store, cursor, response_observed_at, exc)
            raise
        try:
            provider_at, next_at = _provider_timestamps(wrapper, requested_at)
        except HistoricalTimestampError as exc:
            _mark_timestamp_error(store, cursor, response_observed_at, exc)
            raise
        raw_uri, payload_hash = store.archive_json(
            "historical_featured",
            wrapper,
            observed_at=response_observed_at,
            identity=f"{cursor['sport_key']}-{provider_at}",
            metadata={"sport_key": str(cursor["sport_key"]), "provider_at": provider_at},
        )
        _manifest(
            store,
            mode="FEATURED",
            sport_key=str(cursor["sport_key"]),
            requested_at=requested_at,
            provider_at=provider_at,
            raw_uri=raw_uri,
            payload_hash=payload_hash,
            markets=list(FEATURED_GAME_MARKETS),
        )
        cursor["snapshot_at"] = next_at
        cursor["last_provider_at"] = provider_at
        cursor["calls_completed"] = int(cursor.get("calls_completed") or 0) + 1
        cursor["status"] = "RUNNING"
        cursor["last_attempt_at"] = request_started_at
        cursor["last_progress_at"] = response_observed_at
        cursor["updated_at"] = response_observed_at
        cursor.pop("quota_deferred_at", None)
        cursor.pop("last_error", None)
        cursor.pop("last_error_at", None)
        _save_cursor(store, cursor)
        archived.append(raw_uri)
        completed += 1
    return {
        "mode": "FEATURED",
        "calls": completed,
        "provider_calls": completed,
        "call_limit": call_limit,
        "completed_this_cycle": completed_this_cycle,
        "remaining_competitions": sum(
            str(cursor.get("status") or "PENDING") in ACTIVE_CURSOR_STATES
            for cursor in cursors
        ),
        "cursors": [
            {key: value for key, value in cursor.items() if key != "_persisted"}
            for cursor in cursors
        ],
        "archived": archived,
    }


def _additional_market_plan(sport_key: str) -> list[list[str]]:
    market_keys = [
        key
        for key in _market_keys_for_sport(sport_key, SOCCER_MARKET_SEEDS)
        if key not in FEATURED_GAME_MARKETS
    ]
    return [list(batch) for batch in chunks(market_keys, MARKETS_PER_REQUEST)]


def _load_additional_snapshot(
    store: SoccerStore,
    cursor: dict[str, Any],
) -> dict[str, Any]:
    requested_at = iso_utc(str(cursor["snapshot_at"]))
    cursor["snapshot_at"] = requested_at
    request_started_at = iso_utc(now_utc())
    if parse_utc(requested_at) >= now_utc() - timedelta(minutes=5):
        _mark_complete(store, cursor, request_started_at)
        return {"provider_calls": 0, "completed": True, "deferred": False}
    if not store.provider_budget_available(
        "historical_events",
        request_started_at,
        estimated_cost=DEFAULT_MAX_ATTEMPTS,
    ):
        cursor["status"] = "QUOTA_DEFERRED"
        cursor["quota_deferred_at"] = request_started_at
        cursor["last_attempt_at"] = request_started_at
        cursor["updated_at"] = request_started_at
        _save_cursor(store, cursor)
        return {"provider_calls": 0, "completed": False, "deferred": True}
    try:
        response = _client().historical_events(str(cursor["sport_key"]), requested_at)
    except OddsApiError as exc:
        if exc.retryable:
            raise
        failed_at = iso_utc(now_utc())
        _quarantine_provider_scope(
            store,
            cursor,
            operation="historical_events",
            observed_at=failed_at,
            exc=exc,
            scope={"requested_at": requested_at},
        )
        return {
            "provider_calls": 1,
            "completed": True,
            "quarantined": True,
            "deferred": False,
        }
    response_observed_at = iso_utc(now_utc())
    store.record_quota(
        response,
        operation="historical_events",
        observed_at=response_observed_at,
    )
    try:
        wrapper = _validated_wrapper(
            response.data,
            operation="historical_events",
        )
    except HistoricalWrapperSchemaError as exc:
        _mark_schema_error(store, cursor, response_observed_at, exc)
        raise
    try:
        provider_at, next_at = _provider_timestamps(wrapper, requested_at)
    except HistoricalTimestampError as exc:
        _mark_timestamp_error(store, cursor, response_observed_at, exc)
        raise
    raw_uri, payload_hash = store.archive_json(
        "historical_events",
        wrapper,
        observed_at=response_observed_at,
        identity=f"{cursor['sport_key']}-{provider_at}",
        metadata={"sport_key": str(cursor["sport_key"]), "provider_at": provider_at},
    )
    _manifest(
        store,
        mode="EVENTS",
        sport_key=str(cursor["sport_key"]),
        requested_at=requested_at,
        provider_at=provider_at,
        raw_uri=raw_uri,
        payload_hash=payload_hash,
    )
    market_plan = _additional_market_plan(str(cursor["sport_key"]))
    cursor["pending_provider_at"] = provider_at
    cursor["pending_requested_at"] = requested_at
    cursor["pending_next_timestamp"] = next_at
    cursor["pending_events"] = [
        {
            key: event.get(key)
            for key in ("id", "sport_key", "commence_time", "home_team", "away_team")
        }
        for event in (wrapper.get("data") or [])
        if event.get("id")
    ]
    cursor["pending_event_index"] = 0
    cursor["pending_market_index"] = 0
    cursor["pending_market_plan"] = market_plan
    cursor["pending_market_plan_digest"] = digest(market_plan)
    cursor["status"] = "RUNNING"
    cursor["last_provider_at"] = provider_at
    cursor["last_attempt_at"] = request_started_at
    cursor["last_progress_at"] = response_observed_at
    cursor["updated_at"] = response_observed_at
    cursor.pop("quota_deferred_at", None)
    cursor.pop("last_error", None)
    cursor.pop("last_error_at", None)
    # Checkpoint the exact event and market plan before the first event-odds
    # request. A timeout can now resume without re-fetching or changing scope.
    _save_cursor(store, cursor)
    return {
        "provider_calls": 1,
        "completed": False,
        "deferred": False,
        "archived": raw_uri,
    }


def _clear_pending_snapshot(cursor: dict[str, Any]) -> None:
    cursor["snapshot_at"] = iso_utc(
        str(cursor.get("pending_next_timestamp") or cursor["snapshot_at"])
    )
    cursor["pending_events"] = []
    cursor["pending_event_index"] = 0
    cursor["pending_market_index"] = 0
    for key in (
        "pending_provider_at",
        "pending_requested_at",
        "pending_next_timestamp",
        "pending_market_plan",
        "pending_market_plan_digest",
    ):
        cursor.pop(key, None)


def run_additional(
    store: SoccerStore,
    *,
    max_calls: int | None = None,
    sport_key: str | None = None,
) -> dict[str, Any]:
    competitions = _competitions(store)
    if sport_key:
        competitions = [row for row in competitions if row["sport_key"] == sport_key]
    cursors = _cursor_rows(store, "ADDITIONAL", HISTORICAL_ADDITIONAL_START, competitions)
    call_limit = min(
        MAX_CALLS_PER_INVOCATION,
        MAX_CALLS_PER_INVOCATION if max_calls is None else max(0, int(max_calls)),
    )
    if not competitions:
        return {"mode": "ADDITIONAL", "calls": 0, "provider_calls": 0, "reason": "NO_COMPETITIONS"}
    if call_limit == 0:
        return {"mode": "ADDITIONAL", "calls": 0, "provider_calls": 0, "reason": "CALL_LIMIT_ZERO"}
    cursor = _next_active_cursor(cursors)
    # Move already-current competitions to a terminal state without allowing
    # the old wrap-to-start behavior. Continue to another due league in the
    # same invocation so a completion transition does not waste the schedule.
    transitions = len(cursors)
    provider_calls = 0
    archived: list[str] = []
    while cursor is not None and transitions > 0 and not cursor.get("pending_events"):
        transitions -= 1
        loaded = _load_additional_snapshot(store, cursor)
        provider_calls += int(loaded.get("provider_calls") or 0)
        if loaded.get("archived"):
            archived.append(str(loaded["archived"]))
        if loaded.get("deferred"):
            return {
                "mode": "ADDITIONAL",
                "calls": 0,
                "provider_calls": provider_calls,
                "call_limit": call_limit,
                "cursor": {key: value for key, value in cursor.items() if key != "_persisted"},
                "archived": archived,
                "deferred": True,
            }
        if provider_calls >= call_limit:
            return {
                "mode": "ADDITIONAL",
                "calls": 0,
                "provider_calls": provider_calls,
                "call_limit": call_limit,
                "cursor": {key: value for key, value in cursor.items() if key != "_persisted"},
                "archived": archived,
            }
        if loaded.get("completed"):
            cursor = _next_active_cursor(cursors)
            continue
        if not cursor.get("pending_events"):
            # An empty historical event snapshot is complete and can advance
            # immediately without manufacturing event-odds work.
            _clear_pending_snapshot(cursor)
            cursor["updated_at"] = iso_utc(now_utc())
            _save_cursor(store, cursor)
            cursor = _next_active_cursor(cursors)
            continue
        break
    if cursor is None:
        return {
            "mode": "ADDITIONAL",
            "calls": 0,
            "provider_calls": provider_calls,
            "call_limit": call_limit,
            "reason": "COMPLETE",
            "archived": archived,
        }

    pending = list(cursor.get("pending_events") or [])
    index = int(cursor.get("pending_event_index") or 0)
    pending_market_index = int(cursor.get("pending_market_index") or 0)
    market_plan = cursor.get("pending_market_plan")
    if market_plan is None:
        # One-time migration for a cursor created before plans were persisted.
        market_plan = _additional_market_plan(str(cursor["sport_key"]))
        cursor["pending_market_plan"] = market_plan
        cursor["pending_market_plan_digest"] = digest(market_plan)
        _save_cursor(store, cursor)
    else:
        market_plan = [list(batch) for batch in market_plan]
        if cursor.get("pending_market_plan_digest") != digest(market_plan):
            exc = HistoricalTimestampError("persisted historical market plan digest mismatch")
            _mark_timestamp_error(store, cursor, iso_utc(now_utc()), exc)
            raise exc

    event_odds_calls = 0
    events_completed_this_cycle = 0
    client = _client()
    while (
        index < len(pending)
        and events_completed_this_cycle < ADDITIONAL_EVENTS_PER_CYCLE
        and provider_calls < call_limit
    ):
        event = pending[index]
        sport_key = str(cursor["sport_key"])
        provider_at = iso_utc(str(cursor["pending_provider_at"]))
        for market_offset in range(pending_market_index, len(market_plan)):
            if provider_calls >= call_limit:
                break
            market_batch = list(market_plan[market_offset])
            request_started_at = iso_utc(now_utc())
            if not store.provider_budget_available(
                "historical_event_odds",
                request_started_at,
                estimated_cost=(
                    DEFAULT_MAX_ATTEMPTS
                    * 10
                    * len(market_batch)
                    * len(ALL_BOOKMAKER_REGIONS)
                ),
            ):
                cursor["status"] = "QUOTA_DEFERRED"
                cursor["quota_deferred_at"] = request_started_at
                cursor["pending_event_index"] = index
                cursor["pending_market_index"] = market_offset
                cursor["last_attempt_at"] = request_started_at
                cursor["updated_at"] = request_started_at
                _save_cursor(store, cursor)
                return {
                    "mode": "ADDITIONAL",
                    "calls": event_odds_calls,
                    "provider_calls": provider_calls,
                    "call_limit": call_limit,
                    "cursor": {key: value for key, value in cursor.items() if key != "_persisted"},
                    "archived": archived,
                    "deferred": True,
                }
            try:
                response = client.historical_event_odds(
                    sport_key,
                    str(event["id"]),
                    provider_at,
                    market_batch,
                    regions=ALL_BOOKMAKER_REGIONS,
                )
            except OddsApiError as exc:
                if exc.retryable:
                    raise
                failed_at = iso_utc(now_utc())
                provider_calls += 1
                event_odds_calls += 1
                store.ops.put_item(
                    Item=ddb_safe(
                        {
                            "PK": f"HISTORICAL_SCOPE_FAILURE#{sport_key}",
                            "SK": (
                                f"{failed_at}#historical_event_odds#"
                                f"{digest({'event_id': event['id'], 'markets': market_batch})[:16]}"
                            ),
                            "entity_type": "SOCCER_HISTORICAL_SCOPE_FAILURE",
                            "sport_key": sport_key,
                            "mode": "ADDITIONAL",
                            "operation": "historical_event_odds",
                            "event_id": str(event["id"]),
                            "provider_at": provider_at,
                            "markets": market_batch,
                            "detail": str(exc)[:2000],
                            "status_code": exc.status_code,
                            "observed_at": failed_at,
                            "training_eligible": False,
                        }
                    )
                )
                cursor["pending_event_index"] = index
                cursor["pending_market_index"] = market_offset + 1
                cursor["skipped_scope_count"] = int(
                    cursor.get("skipped_scope_count") or 0
                ) + 1
                cursor["last_scope_error_at"] = failed_at
                cursor["updated_at"] = failed_at
                _save_cursor(store, cursor)
                continue
            response_observed_at = iso_utc(now_utc())
            store.record_quota(
                response,
                operation="historical_event_odds",
                observed_at=response_observed_at,
            )
            try:
                wrapper = _validated_wrapper(
                    response.data,
                    operation="historical_event_odds",
                )
            except HistoricalWrapperSchemaError as exc:
                _mark_schema_error(store, cursor, response_observed_at, exc)
                raise
            # Historical event-odds is pinned to the already validated provider
            # snapshot. Normalize any returned timestamp and reject a mismatch.
            if wrapper.get("timestamp") and iso_utc(str(wrapper["timestamp"])) != provider_at:
                exc = HistoricalTimestampError(
                    "historical event-odds timestamp changed within a persisted snapshot"
                )
                _mark_timestamp_error(store, cursor, response_observed_at, exc)
                raise exc
            raw_uri, payload_hash = store.archive_json(
                "historical_event_odds",
                wrapper,
                observed_at=response_observed_at,
                identity=f"{sport_key}-{event['id']}-{provider_at}-{'-'.join(market_batch)}",
                metadata={
                    "sport_key": sport_key,
                    "event_id": str(event["id"]),
                    "provider_at": provider_at,
                },
            )
            _manifest(
                store,
                mode="ADDITIONAL",
                sport_key=sport_key,
                requested_at=iso_utc(str(cursor["pending_requested_at"])),
                provider_at=provider_at,
                raw_uri=raw_uri,
                payload_hash=payload_hash,
                event_id=str(event["id"]),
                markets=market_batch,
            )
            provider_calls += 1
            event_odds_calls += 1
            archived.append(raw_uri)
            cursor["pending_event_index"] = index
            cursor["pending_market_index"] = market_offset + 1
            cursor["calls_completed"] = int(cursor.get("calls_completed") or 0) + 1
            cursor["status"] = "RUNNING"
            cursor["last_attempt_at"] = request_started_at
            cursor["last_progress_at"] = response_observed_at
            cursor["updated_at"] = response_observed_at
            cursor.pop("quota_deferred_at", None)
            cursor.pop("last_error", None)
            cursor.pop("last_error_at", None)
            _save_cursor(store, cursor)
        if int(cursor.get("pending_market_index") or 0) < len(market_plan):
            break
        index += 1
        events_completed_this_cycle += 1
        pending_market_index = 0
        cursor["pending_event_index"] = index
        cursor["pending_market_index"] = 0
        _save_cursor(store, cursor)
    cursor["pending_event_index"] = index
    if index >= len(pending):
        _clear_pending_snapshot(cursor)
    cursor["updated_at"] = iso_utc(now_utc())
    _save_cursor(store, cursor)
    return {
        "mode": "ADDITIONAL",
        "calls": event_odds_calls,
        "provider_calls": provider_calls,
        "call_limit": call_limit,
        "cursor": {key: value for key, value in cursor.items() if key != "_persisted"},
        "archived": archived,
    }


def historical_status(store: SoccerStore) -> dict[str, Any]:
    """Return cursor progress using DynamoDB only; never instantiate a client."""
    # Imported lazily because the materializer intentionally reuses the strict
    # raw-wrapper and manifest validators in this module.
    from .historical_materializer import materialization_status

    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq("HISTORICAL_CURSOR"),
        "ConsistentRead": True,
    }
    rows: list[dict[str, Any]] = []
    while True:
        response = store.ops.query(**kwargs)
        rows.extend(plain(row) for row in response.get("Items") or [])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("FEATURED", "ADDITIONAL"):
        mode_rows = [row for row in rows if str(row.get("mode") or "").upper() == mode]
        by_mode[mode.lower()] = {
            "cursors": len(mode_rows),
            "running": sum(str(row.get("status") or "PENDING") in ACTIVE_CURSOR_STATES for row in mode_rows),
            "complete": sum(row.get("status") == "COMPLETE" for row in mode_rows),
            "quarantined": sum(str(row.get("status") or "").startswith("QUARANTINED") for row in mode_rows),
            "calls_completed": sum(int(row.get("calls_completed") or 0) for row in mode_rows),
            "rows": sorted(mode_rows, key=lambda row: str(row.get("sport_key") or "")),
        }
    return {
        "ok": True,
        "system": "soccer_auto",
        "component": "historical_backfill",
        "enabled": _enabled(),
        "provider_calls": 0,
        "modes": by_mode,
        "supervised_materialization": materialization_status(store),
    }


def _event_max_calls(event: Mapping[str, Any]) -> int:
    raw = event.get("max_calls")
    if raw is None:
        return MAX_CALLS_PER_INVOCATION
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("historical max_calls must be an integer") from exc
    if value < 0:
        raise ValueError("historical max_calls cannot be negative")
    return min(value, MAX_CALLS_PER_INVOCATION)


def historical_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    payload = dict(event or {})
    mode = str(payload.get("mode") or "both").lower()
    if mode == "status":
        return historical_status(store)
    if not _enabled():
        return {
            "ok": False,
            "system": "soccer_auto",
            "component": "historical_backfill",
            "enabled": False,
            "provider_calls": 0,
            "reason": "SOCCER_AUTO_HISTORICAL_BACKFILL_DISABLED",
        }
    if mode not in {"both", "featured", "additional", "materialize"}:
        raise ValueError("unsupported historical backfill mode")
    remaining = _event_max_calls(payload)
    sport_key = str(payload.get("sport_key") or "").strip() or None
    if sport_key is not None and not sport_key.startswith("soccer_"):
        raise ValueError("historical sport_key must be soccer-only")
    result: dict[str, Any] = {
        "ok": True,
        "system": "soccer_auto",
        "component": "historical_backfill",
        "enabled": True,
        "max_calls": remaining,
    }
    if mode == "materialize":
        from .historical_materializer import run_materialization

        event_key = str(payload.get("event_key") or "").strip() or None
        if event_key is not None and not event_key.startswith("EVENT#soccer_"):
            raise ValueError("historical event_key must be soccer-only")
        requested_events = int(payload.get("max_events", remaining))
        if requested_events < 0:
            raise ValueError("historical max_events cannot be negative")
        result["materialization"] = run_materialization(
            store,
            max_events=min(remaining, requested_events),
            event_key=event_key,
        )
        remaining -= int(result["materialization"].get("provider_calls") or 0)
    if mode in {"both", "featured"}:
        result["featured"] = run_featured(
            store, max_calls=remaining, sport_key=sport_key
        )
        remaining -= int(result["featured"].get("provider_calls") or 0)
    if mode in {"both", "additional"}:
        result["additional"] = run_additional(
            store, max_calls=remaining, sport_key=sport_key
        )
        remaining -= int(result["additional"].get("provider_calls") or 0)
    result["provider_calls"] = int(result.get("max_calls") or 0) - max(0, remaining)
    return result
