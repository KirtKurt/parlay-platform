"""Leakage-safe historical T-45 rows joined to authoritative final scores.

The Odds API historical endpoints contain prices, not outcomes.  This module
therefore materializes only events that already have an immutable, validated
``SOCCER_FINAL_SETTLEMENT`` from the provider's separate scores endpoint.
Older odds-only archives remain quarantined until a real historical results
source is configured; prices are never converted into labels.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
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
from .inference import live_lock_coverage_provenance_valid
from .market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, compile_features
from .model import CLASSES, TrainingRow
from .odds_api import DEFAULT_MAX_ATTEMPTS, OddsApiError
from .settlement import (
    settlement_conflict_blocks_training,
    settlement_training_admissible,
    settlement_training_evidence_valid,
    settlement_training_views,
)
from .storage import SoccerStore, ddb_safe, now_utc, plain


MATERIALIZATION_VERSION = "soccer-auto-historical-t45-v1"
HISTORICAL_LOCK_VERSION = "soccer-auto-historical-t45-lock-v1"
HISTORICAL_TRAINING_MANIFEST_VERSION = (
    "soccer-auto-historical-training-manifest-v1"
)
HISTORICAL_LOCK_SOURCE_SUFFIX = "#SOURCE#HISTORICAL"
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


def live_training_lock_key(
    schedule_revision: int,
    target: str = "result_1x2",
) -> str:
    return (
        f"LOCK#T45#REV#{int(schedule_revision)}#TARGET#{target}"
    )


def historical_training_lock_key(
    schedule_revision: int,
    target: str = "result_1x2",
) -> str:
    """Return the separate immutable key for retrospective T45 evidence.

    A live T45 lock may already exist but be unsuitable for supervised
    training because it predates coverage certificates or other provenance
    requirements.  Retrospective evidence must never overwrite that live
    record, so it occupies a distinct item under the same event partition.
    """
    return (
        f"{live_training_lock_key(schedule_revision, target)}"
        f"{HISTORICAL_LOCK_SOURCE_SUFFIX}"
    )


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


def _validated_settlements(store: SoccerStore) -> list[dict[str, Any]]:
    rows = list(store.scan_all(store.settlements, ConsistentRead=True))
    return sorted(
        [
            {
                **row,
                "schedule_identity": str(
                    row.get("schedule_identity") or schedule_identity(row)
                ),
            }
            for row in settlement_training_views(rows)
            if settlement_training_evidence_valid(row)
        ],
        key=lambda row: (str(row.get("commence_time") or ""), str(row["event_key"])),
        reverse=True,
    )


def _authoritative_settlements(store: SoccerStore) -> list[dict[str, Any]]:
    return [
        row for row in _validated_settlements(store)
        if settlement_training_admissible(row)
    ]


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


def lock_has_historical_signals(lock: Mapping[str, Any]) -> bool:
    """Identify every lock that must pass retrospective provenance checks."""
    return bool(
        "historical_materialization" in lock
        or "materialization_version" in lock
        or "materialization_digest" in lock
        or "source_settlement_digest" in lock
        or lock.get("lock_version") == HISTORICAL_LOCK_VERSION
        or lock.get("retrospective_only") is True
    )


def historical_lock_provenance_valid(
    lock: Mapping[str, Any], settlement: Mapping[str, Any]
) -> bool:
    """Prove that a retrospective lock is pre-match and label-independent."""
    if not lock_has_historical_signals(lock):
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
        expected_lock_sk = historical_training_lock_key(
            int(settlement["schedule_revision"])
        )
        return bool(
            settlement_training_admissible(settlement)
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


@dataclass(frozen=True)
class TrainingCandidate:
    row: TrainingRow
    historical_manifest_entry: dict[str, Any] | None


def _training_schedule_identity(
    row: Mapping[str, Any],
) -> tuple[str, int, str, str] | None:
    try:
        revision = int(row.get("schedule_revision") or 0)
        if revision <= 0:
            return None
        identity = str(row.get("schedule_identity") or "")
        if not identity:
            try:
                identity = schedule_identity(row)
            except (KeyError, TypeError, ValueError):
                identity = "LEGACY_IDENTITY_UNAVAILABLE"
        return (
            str(row["event_key"]),
            revision,
            iso_utc(str(row["commence_time"])),
            identity,
        )
    except (KeyError, TypeError, ValueError):
        return None


def training_candidate(
    lock: Mapping[str, Any],
    settlement: Mapping[str, Any],
    *,
    conflicted: bool,
) -> tuple[TrainingCandidate | None, str | None]:
    """Apply the one authoritative post-filter used by status and trainer."""
    if lock.get("entity_type") != "SOCCER_FROZEN_FEATURE_LOCK":
        return None, "invalid"
    if not settlement_training_admissible(settlement):
        return None, "settlement_ineligible"
    if conflicted:
        return None, "settlement_conflict"
    lock_schedule = _training_schedule_identity(lock)
    if (
        lock_schedule is None
        or lock_schedule != _training_schedule_identity(settlement)
    ):
        return None, "schedule_mismatch"
    try:
        expected_lock_sk = (
            historical_training_lock_key(
                int(settlement["schedule_revision"])
            )
            if lock_has_historical_signals(lock)
            else live_training_lock_key(
                int(settlement["schedule_revision"])
            )
        )
        if (
            str(lock.get("PK") or "") != str(settlement["event_key"])
            or str(lock.get("SK") or "") != expected_lock_sk
            or str(lock.get("target") or "") != "result_1x2"
        ):
            return None, "invalid"
    except (KeyError, TypeError, ValueError):
        return None, "invalid"
    if lock.get("training_eligible") is not True:
        return None, "lock_ineligible"
    if not historical_lock_provenance_valid(lock, settlement):
        return None, "historical_provenance"
    features = lock.get("frozen_features")
    if not isinstance(features, Mapping):
        return None, "invalid"
    try:
        schema_matches = (
            lock.get("feature_schema_version") == FEATURE_SCHEMA_VERSION
            and tuple(features.get("feature_names") or ())
            == tuple(FEATURE_NAMES)
            and len(features.get("values") or ()) == len(FEATURE_NAMES)
        )
    except (TypeError, ValueError):
        return None, "invalid"
    if not schema_matches:
        return None, "schema_mismatch"
    if (
        not lock_has_historical_signals(lock)
        and not live_lock_coverage_provenance_valid(lock)
    ):
        return None, "live_provenance"
    try:
        row = TrainingRow(
            event_key=str(lock["event_key"]),
            commence_time=str(lock["commence_time"]),
            feature_hash=str(lock["feature_hash"]),
            features=tuple(float(value) for value in features["values"]),
            market_prior=tuple(
                float(value) for value in features["market_prior"]
            ),
            label=CLASSES.index(str(settlement["result_1x2"])),
            competition=str(lock["sport_key"]),
        )
        historical_entry = None
        if lock_has_historical_signals(lock):
            historical_entry = {
                "PK": str(lock["PK"]),
                "SK": str(lock["SK"]),
                "schedule_identity": str(lock["schedule_identity"]),
                "feature_schema_version": str(lock["feature_schema_version"]),
                "feature_hash": str(lock["feature_hash"]),
                "materialization_digest": str(lock["materialization_digest"]),
                "source_settlement_digest": str(
                    lock["source_settlement_digest"]
                ),
            }
    except (KeyError, TypeError, ValueError):
        return None, "invalid"
    return TrainingCandidate(row, historical_entry), None


def select_training_candidate(
    locks: list[Mapping[str, Any]],
    settlement: Mapping[str, Any],
    *,
    conflicted: bool,
) -> dict[str, Any]:
    """Select one deterministic training authority for an event revision.

    Valid live T45 evidence is preferred because it was captured prospectively.
    A certified retrospective lock is a fallback when the immutable live lock
    is absent or training-ineligible.  Multiple valid locks fail closed rather
    than adding the same label twice.
    """
    valid: list[tuple[int, str, Mapping[str, Any], TrainingCandidate]] = []
    invalid_live_reasons: dict[str, int] = {}
    invalid_historical_reasons: dict[str, int] = {}
    for lock in locks:
        candidate, reason = training_candidate(
            lock,
            settlement,
            conflicted=conflicted,
        )
        historical = lock_has_historical_signals(lock)
        if candidate is not None:
            valid.append(
                (
                    1 if historical else 0,
                    str(lock.get("SK") or ""),
                    lock,
                    candidate,
                )
            )
            continue
        bucket = (
            invalid_historical_reasons
            if historical
            else invalid_live_reasons
        )
        reason_key = str(reason or "invalid")
        bucket[reason_key] = int(bucket.get(reason_key) or 0) + 1
    valid.sort(key=lambda row: (row[0], row[1]))
    duplicate_eligible_locks = max(0, len(valid) - 1)
    selected_lock = valid[0][2] if valid else None
    selected_candidate = valid[0][3] if valid else None
    return {
        "candidate": selected_candidate,
        "lock": selected_lock,
        "duplicate_eligible_locks": duplicate_eligible_locks,
        "invalid_live_reasons": invalid_live_reasons,
        "invalid_historical_reasons": invalid_historical_reasons,
    }


def historical_training_manifest(
    entries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an order-independent proof of historical rows admitted to ML."""
    normalized = sorted(
        (
            {
                "PK": str(entry["PK"]),
                "SK": str(entry["SK"]),
                "schedule_identity": str(entry["schedule_identity"]),
                "feature_schema_version": str(
                    entry["feature_schema_version"]
                ),
                "feature_hash": str(entry["feature_hash"]),
                "materialization_digest": str(
                    entry["materialization_digest"]
                ),
                "source_settlement_digest": str(
                    entry["source_settlement_digest"]
                ),
            }
            for entry in entries
        ),
        key=lambda entry: (entry["PK"], entry["SK"]),
    )
    payload = {
        "version": HISTORICAL_TRAINING_MANIFEST_VERSION,
        "rows": normalized,
    }
    return {
        **payload,
        "count": len(normalized),
        "digest": digest(payload),
    }


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
        "SK": historical_training_lock_key(
            int(settlement["schedule_revision"])
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


def _record_existing_lock(
    result: dict[str, Any],
    lock: Mapping[str, Any],
    settlement: Mapping[str, Any],
) -> None:
    result["existing_locks"] += 1
    candidate, reason = training_candidate(
        lock,
        settlement,
        conflicted=False,
    )
    if candidate is not None:
        result["existing_training_eligible_locks"] += 1
        bucket = (
            "existing_historical_training_locks"
            if candidate.historical_manifest_entry is not None
            else "existing_live_training_locks"
        )
        result[bucket] += 1
        return
    result["invalid_existing_locks"] += 1
    reason_key = str(reason or "invalid")
    reasons = result["invalid_existing_lock_reasons"]
    reasons[reason_key] = int(reasons.get(reason_key) or 0) + 1


def _record_nontraining_live_lock(
    result: dict[str, Any],
    lock: Mapping[str, Any],
    settlement: Mapping[str, Any],
) -> None:
    """Record a legacy/live exclusion without treating it as corruption.

    The immutable live row remains auditable.  It simply does not block a
    separate retrospective lock from supplying certified training evidence.
    """
    candidate, reason = training_candidate(
        lock,
        settlement,
        conflicted=False,
    )
    if candidate is not None:
        raise RuntimeError("valid live lock passed to nontraining recorder")
    result["nontraining_live_locks"] += 1
    reason_key = str(reason or "invalid")
    reasons = result["nontraining_live_lock_reasons"]
    reasons[reason_key] = int(reasons.get(reason_key) or 0) + 1


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
        "existing_training_eligible_locks": 0,
        "existing_historical_training_locks": 0,
        "existing_live_training_locks": 0,
        "nontraining_live_locks": 0,
        "nontraining_live_lock_reasons": {},
        "invalid_existing_locks": 0,
        "invalid_existing_lock_reasons": {},
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
        historical_existing = store.get_lock(
            str(settlement["event_key"]),
            schedule_revision=int(settlement["schedule_revision"]),
            historical=True,
        )
        if historical_existing:
            _record_existing_lock(
                result,
                historical_existing,
                settlement,
            )
            continue
        live_existing = store.get_lock(
            str(settlement["event_key"]),
            schedule_revision=int(settlement["schedule_revision"]),
        )
        if live_existing:
            live_candidate, _reason = training_candidate(
                live_existing,
                settlement,
                conflicted=False,
            )
            if live_candidate is not None:
                _record_existing_lock(result, live_existing, settlement)
                continue
            _record_nontraining_live_lock(
                result,
                live_existing,
                settlement,
            )
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
            candidate, reason = training_candidate(
                lock,
                settlement,
                conflicted=False,
            )
            if candidate is None or candidate.historical_manifest_entry is None:
                raise RuntimeError(
                    "built historical lock failed training proof: "
                    f"{reason or 'historical_manifest_missing'}"
                )
            # A live freeze may have completed while the historical provider
            # request was in flight.  Prefer that prospective evidence when it
            # now passes the current training contract.
            concurrent_live = store.get_lock(
                str(settlement["event_key"]),
                schedule_revision=int(settlement["schedule_revision"]),
            )
            if concurrent_live:
                live_candidate, _reason = training_candidate(
                    concurrent_live,
                    settlement,
                    conflicted=False,
                )
                if live_candidate is not None:
                    _record_existing_lock(
                        result,
                        concurrent_live,
                        settlement,
                    )
                    continue
            if not store.put_lock(lock):
                winner = store.get_lock(
                    str(settlement["event_key"]),
                    schedule_revision=int(settlement["schedule_revision"]),
                    historical=True,
                )
                if not winner:
                    raise RuntimeError(
                        "historical lock conditional write lost without a winner"
                    )
                _record_existing_lock(result, winner, settlement)
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
    validated_settlements = _validated_settlements(store)
    settlements = [
        row for row in validated_settlements
        if settlement_training_admissible(row)
    ]
    conflicted_event_keys = _conflicted_event_keys(store)
    settlements_by_key = {
        (str(row["event_key"]), int(row["schedule_revision"])): row
        for row in settlements
    }
    all_locks = list(store.scan_all(store.locks, ConsistentRead=True))
    locks_by_storage_key = {
        (str(row.get("PK") or ""), str(row.get("SK") or "")): row
        for row in all_locks
    }
    historical_entries: list[dict[str, Any]] = []
    valid_live_existing_locks = 0
    invalid_existing_locks = 0
    invalid_existing_lock_reasons: dict[str, int] = {}
    nontraining_live_locks = 0
    nontraining_live_lock_reasons: dict[str, int] = {}
    duplicate_training_eligible_locks = 0
    selected_lock_keys: set[tuple[str, int]] = set()
    invalid_historical_keys: set[tuple[str, int]] = set()
    for key, settlement in settlements_by_key.items():
        if key[0] in conflicted_event_keys:
            continue
        event_key = str(settlement["event_key"])
        revision = int(settlement["schedule_revision"])
        live = locks_by_storage_key.get(
            (event_key, live_training_lock_key(revision))
        )
        historical = locks_by_storage_key.get(
            (event_key, historical_training_lock_key(revision))
        )
        assessment = select_training_candidate(
            [lock for lock in (live, historical) if lock is not None],
            settlement,
            conflicted=False,
        )
        for reason, count in assessment["invalid_live_reasons"].items():
            nontraining_live_locks += int(count)
            nontraining_live_lock_reasons[reason] = (
                int(nontraining_live_lock_reasons.get(reason) or 0)
                + int(count)
            )
        historical_invalid_count = sum(
            int(count)
            for count in assessment["invalid_historical_reasons"].values()
        )
        if historical_invalid_count:
            invalid_historical_keys.add(key)
        for reason, count in assessment["invalid_historical_reasons"].items():
            invalid_existing_locks += int(count)
            invalid_existing_lock_reasons[reason] = (
                int(invalid_existing_lock_reasons.get(reason) or 0)
                + int(count)
            )
        duplicate_count = int(assessment["duplicate_eligible_locks"])
        if duplicate_count:
            duplicate_training_eligible_locks += duplicate_count
            invalid_existing_locks += duplicate_count
            invalid_existing_lock_reasons["duplicate_training_authority"] = (
                int(
                    invalid_existing_lock_reasons.get(
                        "duplicate_training_authority"
                    )
                    or 0
                )
                + duplicate_count
            )
            continue
        candidate = assessment["candidate"]
        if candidate is None:
            continue
        selected_lock_keys.add(key)
        if candidate.historical_manifest_entry is not None:
            historical_entries.append(candidate.historical_manifest_entry)
        else:
            valid_live_existing_locks += 1
    historical_manifest = historical_training_manifest(historical_entries)
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
    terminal_keys = {
        (str(row.get("event_key") or ""), int(row.get("schedule_revision") or 0))
        for row in states
        if str(row.get("status") or "") in TERMINAL_STATES
    }
    conflict_count = sum(
        event_key in conflicted_event_keys
        for event_key, _revision in settlements_by_key
    )
    terminal_without_lock = sum(
        key not in selected_lock_keys
        and key not in invalid_historical_keys
        and key in terminal_keys
        and key[0] not in conflicted_event_keys
        for key in settlements_by_key
    )
    pending = sum(
        key not in selected_lock_keys
        and key not in invalid_historical_keys
        and key not in terminal_keys
        and key[0] not in conflicted_event_keys
        for key in settlements_by_key
    )
    existing_nonconflicted = sum(
        key in selected_lock_keys and key[0] not in conflicted_event_keys
        for key in settlements_by_key
    )
    return {
        "mode": "AUTHORITATIVE_RESULT_JOINED_T45",
        "validated_settlements": len(validated_settlements),
        "authoritative_settlements": len(settlements),
        "ineligible_validated_settlements": (
            len(validated_settlements) - len(settlements)
        ),
        "materialized_rows": historical_manifest["count"],
        "historical_training_rows": historical_manifest["count"],
        "historical_training_manifest_version": historical_manifest["version"],
        "historical_training_manifest_digest": historical_manifest["digest"],
        "existing_training_eligible_locks": (
            historical_manifest["count"] + valid_live_existing_locks
        ),
        "existing_historical_training_locks": historical_manifest["count"],
        "existing_live_training_locks": valid_live_existing_locks,
        "nontraining_live_locks": nontraining_live_locks,
        "nontraining_live_lock_reasons": nontraining_live_lock_reasons,
        "duplicate_training_eligible_locks": (
            duplicate_training_eligible_locks
        ),
        "invalid_existing_locks": invalid_existing_locks,
        "invalid_existing_lock_reasons": invalid_existing_lock_reasons,
        "conflict_blocked_authoritative_settlements": conflict_count,
        "terminal_authoritative_settlements": terminal_without_lock,
        "pending_authoritative_settlements": pending,
        "classified_authoritative_settlements": (
            existing_nonconflicted
            + len(invalid_historical_keys)
            + conflict_count
            + terminal_without_lock
            + pending
        ),
        "latest_progress_at": max(
            (str(row.get("updated_at") or "") for row in states),
            default=None,
        ),
        "state_rows": len(states),
        "state_rows_truncated": False,
        "state_query_pages": state_query_pages,
        "label_policy": (
            "Only immutable, trainer-eligible The Odds API final-score "
            "settlements are admitted; regulation-ambiguous results and older "
            "odds-only archives remain training-ineligible."
        ),
    }
