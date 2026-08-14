"""Leakage-safe historical T-45 rows joined to authoritative final scores.

The Odds API historical endpoints contain prices, not outcomes.  This module
therefore materializes only events that already have an immutable, validated
``SOCCER_FINAL_SETTLEMENT`` from the provider's separate scores endpoint.
Older odds-only archives remain quarantined until a real historical results
source is configured; prices are never converted into labels.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Mapping

from boto3.dynamodb.conditions import Key

from .canonical import digest, iso_utc, parse_utc, schedule_identity
from .collector import _client
from .config import ALL_BOOKMAKER_REGIONS, FEATURED_GAME_MARKETS
from .historical import (
    HistoricalManifestConflict,
    HistoricalTimestampError,
    HistoricalWrapperSchemaError,
    _manifest,
    _provider_timestamps,
    _validated_wrapper,
)
from .market_features import FEATURE_SCHEMA_VERSION, compile_features
from .odds_api import DEFAULT_MAX_ATTEMPTS, OddsApiError
from .settlement import (
    settlement_conflict_blocks_training,
    settlement_training_evidence_valid,
)
from .storage import SoccerStore, ddb_safe, now_utc, plain


MATERIALIZATION_VERSION = "soccer-auto-historical-t45-v1"
HISTORICAL_LOCK_VERSION = "soccer-auto-historical-t45-lock-v1"
MAX_SNAPSHOT_LAG_MINUTES = 15
MIN_BOOKMAKERS = int(os.getenv("SOCCER_AUTO_MIN_BOOKMAKERS", "3"))
MAX_EVENTS_PER_INVOCATION = min(
    25,
    max(
        1,
        int(
            os.getenv(
                "SOCCER_AUTO_HISTORICAL_MATERIALIZATION_EVENTS_PER_INVOCATION",
                "5",
            )
        ),
    ),
)
TERMINAL_STATES = frozenset(
    {
        "MATERIALIZED_ELIGIBLE",
        "EXISTING_IMMUTABLE_LOCK",
        "INELIGIBLE_MARKET_COVERAGE",
        "EVENT_NOT_PRESENT_AT_T45",
        "PROVIDER_SCOPE_UNAVAILABLE",
        "QUARANTINED_IDENTITY_MISMATCH",
        "QUARANTINED_SNAPSHOT_TIME",
        "QUARANTINED_REPAINT",
    }
)


class HistoricalIdentityMismatch(ValueError):
    """Historical price evidence does not match the settled schedule identity."""


def _state_key(settlement: Mapping[str, Any]) -> dict[str, str]:
    return {
        "PK": "HISTORICAL_MATERIALIZATION",
        "SK": (
            f"{iso_utc(str(settlement['commence_time']))}#"
            f"{settlement['event_key']}#REV#{int(settlement['schedule_revision'])}"
        ),
    }


def _state(store: SoccerStore, settlement: Mapping[str, Any]) -> dict[str, Any]:
    row = store.ops.get_item(
        Key=_state_key(settlement), ConsistentRead=True
    ).get("Item")
    return plain(row) if row else {}


def _write_state(
    store: SoccerStore,
    settlement: Mapping[str, Any],
    *,
    status: str,
    observed_at: str,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    previous = _state(store, settlement)
    row = {
        **_state_key(settlement),
        "entity_type": "SOCCER_HISTORICAL_T45_MATERIALIZATION_STATE",
        "materialization_version": MATERIALIZATION_VERSION,
        "event_key": settlement["event_key"],
        "event_id": settlement["event_id"],
        "sport_key": settlement["sport_key"],
        "commence_time": iso_utc(str(settlement["commence_time"])),
        "schedule_revision": int(settlement["schedule_revision"]),
        "schedule_identity": str(settlement["schedule_identity"]),
        "source_settlement_digest": settlement["settlement_digest"],
        "source_settlement_provider": settlement["source"],
        "status": status,
        "attempts": int(previous.get("attempts") or 0) + 1,
        "first_attempt_at": previous.get("first_attempt_at") or observed_at,
        "updated_at": observed_at,
        **dict(detail or {}),
    }
    store.ops.put_item(Item=ddb_safe(row))
    return row


def _authoritative_settlements(store: SoccerStore) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                **row,
                "schedule_identity": str(
                    row.get("schedule_identity") or schedule_identity(row)
                ),
            }
            for row in store.scan_all(store.settlements, ConsistentRead=True)
            if settlement_training_evidence_valid(row)
        ],
        key=lambda row: (str(row.get("commence_time") or ""), str(row["event_key"])),
        reverse=True,
    )


def _conflicted_event_keys(store: SoccerStore) -> set[str]:
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq("SETTLEMENT_CONFLICT"),
        "ConsistentRead": True,
    }
    while True:
        response = store.ops.query(**kwargs)
        rows.extend(plain(item) for item in response.get("Items") or [])
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    return {
        str(row.get("event_key") or "")
        for row in rows
        if row.get("event_key") and settlement_conflict_blocks_training(row)
    }


def historical_lock_provenance_valid(
    lock: Mapping[str, Any], settlement: Mapping[str, Any]
) -> bool:
    """Prove that a retrospective lock is pre-match and label-independent."""
    historical_signals = (
        "historical_materialization" in lock
        or "materialization_version" in lock
        or "materialization_digest" in lock
        or "source_settlement_digest" in lock
        or lock.get("lock_version") == HISTORICAL_LOCK_VERSION
        or lock.get("retrospective_only") is True
    )
    if not historical_signals:
        return True
    try:
        settlement_identity = str(
            settlement.get("schedule_identity") or schedule_identity(settlement)
        )
        lock_at = parse_utc(str(lock["lock_at"]))
        provider_at = parse_utc(str(lock["source_provider_at_max"]))
        retrieved_at = parse_utc(str(lock["source_retrieved_at"]))
        expected_lock_at = parse_utc(str(lock["commence_time"])) - timedelta(
            minutes=45
        )
        source_hashes = list(lock.get("source_payload_hashes") or [])
        source_uris = list(lock.get("source_raw_uris") or [])
        if (
            len(source_hashes) != 1
            or len(source_uris) != 1
            or not str(source_uris[0]).startswith("s3://")
        ):
            return False
        expected_feature_hash = digest(
            {
                "event_key": lock["event_key"],
                "schedule_revision": int(lock["schedule_revision"]),
                "lock_at": iso_utc(lock_at),
                "provider_at": iso_utc(provider_at),
                "source_payload_hash": source_hashes[0],
                "features": lock["frozen_features"],
            }
        )
        expected_materialization_digest = digest(
            {
                "feature_hash": expected_feature_hash,
                "source_settlement_digest": settlement["settlement_digest"],
                "schedule_identity": settlement_identity,
                "source_raw_uri": source_uris[0],
            }
        )
        expected_lock_sk = (
            f"LOCK#T45#REV#{int(settlement['schedule_revision'])}#"
            "TARGET#result_1x2"
        )
        return bool(
            settlement_training_evidence_valid(settlement)
            and lock.get("historical_materialization") is True
            and lock.get("retrospective_only") is True
            and lock.get("immutable") is True
            and lock.get("lock_version") == HISTORICAL_LOCK_VERSION
            and lock.get("materialization_version") == MATERIALIZATION_VERSION
            and lock.get("prediction_eligible") is False
            and lock.get("labels") is None
            and lock.get("training_eligible") is True
            and str(lock.get("PK") or "") == str(settlement["event_key"])
            and str(lock.get("SK") or "") == expected_lock_sk
            and str(lock.get("target") or "") == "result_1x2"
            and str(lock.get("event_key") or "")
            == str(settlement["event_key"])
            and str(lock.get("event_id") or "") == str(settlement["event_id"])
            and str(lock.get("sport_key") or "") == str(settlement["sport_key"])
            and iso_utc(str(lock["commence_time"]))
            == iso_utc(str(settlement["commence_time"]))
            and str(lock.get("home_team") or "")
            == str(settlement.get("home_team") or "")
            and str(lock.get("away_team") or "")
            == str(settlement.get("away_team") or "")
            and lock.get("source_provider_before_lock") is True
            and lock.get("source_observed_before_lock") is False
            and expected_lock_at == lock_at
            and timedelta(0) <= lock_at - provider_at <= timedelta(
                minutes=MAX_SNAPSHOT_LAG_MINUTES
            )
            and retrieved_at > lock_at
            and iso_utc(str(lock["created_at"])) == iso_utc(retrieved_at)
            and iso_utc(str(lock["source_observed_at_max"]))
            == iso_utc(retrieved_at)
            and list(lock.get("source_slot_ids") or [])
            == [f"HISTORICAL_T45#{iso_utc(provider_at)}"]
            and str(lock.get("source_settlement_digest") or "")
            == str(settlement.get("settlement_digest") or "")
            and str(lock.get("source_settlement_provider") or "")
            == str(settlement.get("source") or "")
            and str(lock.get("schedule_identity") or "")
            == settlement_identity
            and schedule_identity(lock) == settlement_identity
            and int(lock.get("schedule_revision") or 0)
            == int(settlement.get("schedule_revision") or 0)
            and str(lock.get("feature_hash") or "") == expected_feature_hash
            and str(lock.get("materialization_digest") or "")
            == expected_materialization_digest
        )
    except (KeyError, TypeError, ValueError):
        return False


def _matching_event(
    wrapper: Mapping[str, Any], settlement: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    matches = [
        row
        for row in wrapper.get("data") or []
        if str(row.get("id") or "") == str(settlement["event_id"])
    ]
    if len(matches) > 1:
        raise HistoricalIdentityMismatch(
            "historical T45 wrapper contains duplicate provider event identity"
        )
    return matches[0] if matches else None


def _assert_nested_updates_prelock(
    event: Mapping[str, Any], *, lock_at: Any
) -> None:
    """Reject any bookmaker/market payload carrying a post-cutoff update."""
    for bookmaker in event.get("bookmakers") or []:
        timestamps = [bookmaker.get("last_update")]
        for market in bookmaker.get("markets") or []:
            timestamps.append(market.get("last_update"))
            timestamps.extend(
                outcome.get("last_update")
                for outcome in market.get("outcomes") or []
                if isinstance(outcome, Mapping)
            )
        for value in timestamps:
            if value and parse_utc(str(value)) > lock_at:
                raise HistoricalTimestampError(
                    "historical bookmaker evidence contains a post-T45 update"
                )


def _build_lock(
    settlement: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    provider_at: str,
    raw_uri: str,
    payload_hash: str,
    observed_at: str,
) -> dict[str, Any]:
    expected_identity = str(settlement.get("schedule_identity") or "")
    if not expected_identity or expected_identity != schedule_identity(settlement):
        raise HistoricalIdentityMismatch(
            "authoritative settlement schedule identity is internally inconsistent"
        )
    if (
        str(event.get("id") or "") != str(settlement["event_id"])
        or str(event.get("sport_key") or "") != str(settlement["sport_key"])
        or schedule_identity(event) != expected_identity
    ):
        raise HistoricalIdentityMismatch(
            "historical T45 event identity does not match authoritative settlement"
        )
    lock_at = parse_utc(str(settlement["commence_time"])) - timedelta(minutes=45)
    provider_time = parse_utc(provider_at)
    lag = lock_at - provider_time
    if lag.total_seconds() < 0 or lag > timedelta(minutes=MAX_SNAPSHOT_LAG_MINUTES):
        raise HistoricalTimestampError(
            "historical odds snapshot is not the closest safe pre-T45 checkpoint"
        )
    _assert_nested_updates_prelock(event, lock_at=lock_at)
    # The exact T45 snapshot is both endpoints for the first safe historical
    # schema. Movement is deliberately zero rather than imported from an
    # unverified or post-cutoff observation.
    features = plain(
        ddb_safe(compile_features(event, earliest=event, hours_to_start=0.75))
    )
    if int(features["book_count"]) < MIN_BOOKMAKERS:
        raise ValueError("insufficient complete three-way bookmaker coverage")
    feature_hash = digest(
        {
            "event_key": settlement["event_key"],
            "schedule_revision": int(settlement["schedule_revision"]),
            "lock_at": iso_utc(lock_at),
            "provider_at": iso_utc(provider_time),
            "source_payload_hash": payload_hash,
            "features": features,
        }
    )
    materialization_digest = digest(
        {
            "feature_hash": feature_hash,
            "source_settlement_digest": settlement["settlement_digest"],
            "schedule_identity": expected_identity,
            "source_raw_uri": raw_uri,
        }
    )
    return {
        "PK": settlement["event_key"],
        "SK": (
            f"LOCK#T45#REV#{int(settlement['schedule_revision'])}#"
            "TARGET#result_1x2"
        ),
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        "lock_version": HISTORICAL_LOCK_VERSION,
        "materialization_version": MATERIALIZATION_VERSION,
        "historical_materialization": True,
        "retrospective_only": True,
        "immutable": True,
        "event_key": settlement["event_key"],
        "event_id": settlement["event_id"],
        "sport_key": settlement["sport_key"],
        "commence_time": iso_utc(str(settlement["commence_time"])),
        "schedule_revision": int(settlement["schedule_revision"]),
        "schedule_identity": expected_identity,
        "home_team": settlement.get("home_team"),
        "away_team": settlement.get("away_team"),
        "target": "result_1x2",
        "lock_at": iso_utc(lock_at),
        "created_at": observed_at,
        "labels": None,
        "training_eligible": True,
        # Retrospective rows may train but can never publish or repaint a pick.
        "prediction_eligible": False,
        "exclusion_reasons": [],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_hash": feature_hash,
        "frozen_features": features,
        "source_slot_ids": [f"HISTORICAL_T45#{iso_utc(provider_time)}"],
        "source_payload_hashes": [payload_hash],
        "source_raw_uris": [raw_uri],
        "source_provider_at_max": iso_utc(provider_time),
        # Keep provider snapshot time separate from the later reconstruction
        # time; retrospective evidence must never masquerade as a live capture.
        "source_observed_at_max": iso_utc(observed_at),
        "source_retrieved_at": iso_utc(observed_at),
        "source_provider_before_lock": provider_time <= lock_at,
        "source_observed_before_lock": parse_utc(observed_at) <= lock_at,
        "source_settlement_digest": settlement["settlement_digest"],
        "source_settlement_provider": settlement["source"],
        "materialization_digest": materialization_digest,
    }


def run_materialization(
    store: SoccerStore,
    *,
    max_events: int | None = None,
    event_key: str | None = None,
) -> dict[str, Any]:
    limit = min(
        MAX_EVENTS_PER_INVOCATION,
        MAX_EVENTS_PER_INVOCATION
        if max_events is None
        else max(0, int(max_events)),
    )
    settlements = _authoritative_settlements(store)
    if event_key:
        settlements = [row for row in settlements if row["event_key"] == event_key]
    conflicts = _conflicted_event_keys(store)
    result: dict[str, Any] = {
        "mode": "MATERIALIZE",
        "authoritative_settlements": len(settlements),
        "event_limit": limit,
        "provider_calls": 0,
        "materialized": 0,
        "existing_locks": 0,
        "terminal_skips": 0,
        "conflict_skips": 0,
        "quota_deferred": False,
        "failures": [],
    }
    client = None
    for settlement in settlements:
        if str(settlement["event_key"]) in conflicts:
            result["conflict_skips"] += 1
            continue
        existing = store.get_lock(
            str(settlement["event_key"]),
            schedule_revision=int(settlement["schedule_revision"]),
        )
        if existing:
            result["existing_locks"] += 1
            continue
        state = _state(store, settlement)
        if str(state.get("status") or "") in TERMINAL_STATES:
            result["terminal_skips"] += 1
            continue
        if int(result["provider_calls"]) >= limit:
            break
        request_started_at = iso_utc(now_utc())
        estimated_cost = (
            DEFAULT_MAX_ATTEMPTS
            * 10
            * len(FEATURED_GAME_MARKETS)
            * len(ALL_BOOKMAKER_REGIONS)
        )
        if not store.provider_budget_available(
            "historical_t45_materialization",
            request_started_at,
            estimated_cost=estimated_cost,
        ):
            _write_state(
                store,
                settlement,
                status="QUOTA_DEFERRED",
                observed_at=request_started_at,
                detail={"training_eligible": False},
            )
            result["quota_deferred"] = True
            break
        lock_at = iso_utc(
            parse_utc(str(settlement["commence_time"])) - timedelta(minutes=45)
        )
        if client is None:
            client = _client()
        try:
            response = client.historical_odds(
                str(settlement["sport_key"]),
                lock_at,
                FEATURED_GAME_MARKETS,
                regions=ALL_BOOKMAKER_REGIONS,
            )
        except OddsApiError as exc:
            if exc.retryable:
                raise
            failed_at = iso_utc(now_utc())
            result["provider_calls"] += 1
            _write_state(
                store,
                settlement,
                status="PROVIDER_SCOPE_UNAVAILABLE",
                observed_at=failed_at,
                detail={
                    "training_eligible": False,
                    "status_code": exc.status_code,
                    "detail": str(exc)[:1000],
                },
            )
            result["failures"].append(
                {"event_key": settlement["event_key"], "reason": "PROVIDER_SCOPE_UNAVAILABLE"}
            )
            continue
        response_observed_at = iso_utc(now_utc())
        result["provider_calls"] += 1
        store.record_quota(
            response,
            operation="historical_t45_materialization",
            observed_at=response_observed_at,
        )
        try:
            wrapper = _validated_wrapper(
                response.data,
                operation="historical_featured",
            )
        except HistoricalWrapperSchemaError:
            _write_state(
                store,
                settlement,
                status="QUARANTINED_PROVIDER_SCHEMA",
                observed_at=response_observed_at,
                detail={"training_eligible": False},
            )
            raise
        try:
            provider_at, _ = _provider_timestamps(wrapper, lock_at)
        except HistoricalTimestampError as exc:
            _write_state(
                store,
                settlement,
                status="QUARANTINED_SNAPSHOT_TIME",
                observed_at=response_observed_at,
                detail={"training_eligible": False, "detail": str(exc)[:1000]},
            )
            result["failures"].append(
                {"event_key": settlement["event_key"], "reason": "QUARANTINED_SNAPSHOT_TIME"}
            )
            continue
        raw_uri, payload_hash = store.archive_json(
            "historical_t45_materialization",
            wrapper,
            observed_at=response_observed_at,
            identity=(
                f"{settlement['sport_key']}-{settlement['event_id']}-{lock_at}"
            ),
            metadata={
                "sport_key": str(settlement["sport_key"]),
                "event_id": str(settlement["event_id"]),
                "lock_at": lock_at,
                "provider_at": provider_at,
            },
        )
        try:
            _manifest(
                store,
                mode="SUPERVISED_T45",
                sport_key=str(settlement["sport_key"]),
                requested_at=lock_at,
                provider_at=provider_at,
                raw_uri=raw_uri,
                payload_hash=payload_hash,
                event_id=str(settlement["event_id"]),
                markets=list(FEATURED_GAME_MARKETS),
            )
            event = _matching_event(wrapper, settlement)
            if event is None:
                _write_state(
                    store,
                    settlement,
                    status="EVENT_NOT_PRESENT_AT_T45",
                    observed_at=response_observed_at,
                    detail={
                        "training_eligible": False,
                        "provider_at": provider_at,
                        "raw_uri": raw_uri,
                    },
                )
                result["failures"].append(
                    {"event_key": settlement["event_key"], "reason": "EVENT_NOT_PRESENT_AT_T45"}
                )
                continue
            lock = _build_lock(
                settlement,
                event,
                provider_at=provider_at,
                raw_uri=raw_uri,
                payload_hash=payload_hash,
                observed_at=response_observed_at,
            )
        except HistoricalTimestampError as exc:
            status = "QUARANTINED_SNAPSHOT_TIME"
            detail = str(exc)
        except HistoricalManifestConflict as exc:
            status = "QUARANTINED_REPAINT"
            detail = str(exc)
        except HistoricalIdentityMismatch as exc:
            status = "QUARANTINED_IDENTITY_MISMATCH"
            detail = str(exc)
        except ValueError as exc:
            status = "INELIGIBLE_MARKET_COVERAGE"
            detail = str(exc)
        else:
            if not store.put_lock(lock):
                result["existing_locks"] += 1
                continue
            _write_state(
                store,
                settlement,
                status="MATERIALIZED_ELIGIBLE",
                observed_at=response_observed_at,
                detail={
                    "training_eligible": True,
                    "provider_at": provider_at,
                    "feature_hash": lock["feature_hash"],
                    "materialization_digest": lock["materialization_digest"],
                    "raw_uri": raw_uri,
                },
            )
            result["materialized"] += 1
            continue
        _write_state(
            store,
            settlement,
            status=status,
            observed_at=response_observed_at,
            detail={
                "training_eligible": False,
                "provider_at": provider_at,
                "detail": detail[:1000],
                "raw_uri": raw_uri,
            },
        )
        result["failures"].append(
            {"event_key": settlement["event_key"], "reason": status}
        )
    return result


def materialization_status(store: SoccerStore) -> dict[str, Any]:
    settlements = _authoritative_settlements(store)
    conflicted_event_keys = _conflicted_event_keys(store)
    settlements_by_key = {
        (str(row["event_key"]), int(row["schedule_revision"])): row
        for row in settlements
    }
    all_locks = [
        row
        for row in store.scan_all(store.locks, ConsistentRead=True)
        if row.get("entity_type") == "SOCCER_FROZEN_FEATURE_LOCK"
    ]
    historical_locks = [
        row for row in all_locks if row.get("historical_materialization") is True
    ]
    joined = [
        row
        for row in historical_locks
        if (
            settlement := settlements_by_key.get(
                (
                    str(row.get("event_key") or ""),
                    int(row.get("schedule_revision") or 0),
                )
            )
        )
        and historical_lock_provenance_valid(row, settlement)
        and str(row.get("event_key") or "") not in conflicted_event_keys
    ]
    states: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq("HISTORICAL_MATERIALIZATION"),
        "ConsistentRead": True,
    }
    state_query_pages = 0
    while True:
        response = store.ops.query(**kwargs)
        state_query_pages += 1
        states.extend(plain(row) for row in response.get("Items") or [])
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            break
        kwargs["ExclusiveStartKey"] = cursor
    lock_keys = {
        (str(row.get("event_key") or ""), int(row.get("schedule_revision") or 0))
        for row in all_locks
    }
    terminal_keys = {
        (str(row.get("event_key") or ""), int(row.get("schedule_revision") or 0))
        for row in states
        if str(row.get("status") or "") in TERMINAL_STATES
    }
    return {
        "mode": "AUTHORITATIVE_RESULT_JOINED_T45",
        "authoritative_settlements": len(settlements),
        "materialized_rows": len(joined),
        "historical_training_rows": sum(
            row.get("training_eligible") is True for row in joined
        ),
        "conflict_blocked_authoritative_settlements": sum(
            event_key in conflicted_event_keys
            for event_key, _revision in settlements_by_key
        ),
        "pending_authoritative_settlements": sum(
            key not in lock_keys
            and key not in terminal_keys
            and key[0] not in conflicted_event_keys
            for key in settlements_by_key
        ),
        "latest_progress_at": max(
            (str(row.get("updated_at") or "") for row in states),
            default=None,
        ),
        "state_rows": len(states),
        "state_rows_truncated": False,
        "state_query_pages": state_query_pages,
        "label_policy": (
            "Only immutable The Odds API final-score settlements are admitted; "
            "older odds-only archives remain training-ineligible."
        ),
    }
