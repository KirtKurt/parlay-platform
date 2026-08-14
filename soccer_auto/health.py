"""Evidence-backed health proof for soccer training and T10 decisions."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping

from boto3.dynamodb.conditions import Key

from .canonical import iso_utc, parse_utc, schedule_identity
from .config import (
    FINAL_DECISION_CAPTURE_LEAD_SECONDS,
    LOCK_VERSION_BY_HORIZON,
    PUBLIC_DECISION_HORIZON,
    PUBLIC_PREDICTION_BINDING_VERSION,
    PUBLICATION_COMMIT_HEADROOM_SECONDS,
    PUBLICATION_CUTOFF_MINUTES,
    TRAINING_LOCK_HORIZON,
)
from .historical_materializer import (
    historical_training_lock_key,
    select_training_candidate,
)
from .inference import live_lock_coverage_provenance_valid, lock_key
from .settlement import (
    settlement_conflict_blocks_training,
    settlement_training_admissible,
    settlement_training_evidence_valid,
    settlement_training_views,
)
from .storage import SoccerStore, now_utc, plain


HEALTH_CONTRACT_VERSION = "soccer-auto-health-proof-v1"
HEALTH_SCAN_LIMIT = 2000
RECENT_DECISION_AUDIT_HOURS = 24
UPCOMING_DECISION_AUDIT_HOURS = 24


def _scan(table: Any, *, limit: int = HEALTH_SCAN_LIMIT) -> tuple[list[dict[str, Any]], bool]:
    response = table.scan(ConsistentRead=True, Limit=limit)
    return [plain(row) for row in response.get("Items") or []], bool(
        response.get("LastEvaluatedKey")
    )


def _conflicted_events(store: SoccerStore) -> tuple[set[str], bool]:
    if not hasattr(store.ops, "query"):
        rows, truncated = _scan(store.ops)
    else:
        response = store.ops.query(
            KeyConditionExpression=Key("PK").eq("SETTLEMENT_CONFLICT"),
            ConsistentRead=True,
            Limit=HEALTH_SCAN_LIMIT,
        )
        rows = [plain(row) for row in response.get("Items") or []]
        truncated = bool(response.get("LastEvaluatedKey"))
    return (
        {
            str(row.get("event_key") or "")
            for row in rows
            if row.get("event_key") and settlement_conflict_blocks_training(row)
        },
        truncated,
    )


def _same_schedule(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        left_identity = str(left.get("schedule_identity") or schedule_identity(left))
        right_identity = str(right.get("schedule_identity") or schedule_identity(right))
        return bool(
            int(left.get("schedule_revision") or 0) > 0
            and int(left.get("schedule_revision") or 0)
            == int(right.get("schedule_revision") or 0)
            and left_identity == schedule_identity(left)
            and right_identity == schedule_identity(right)
            and left_identity == right_identity
            and iso_utc(str(left.get("commence_time") or ""))
            == iso_utc(str(right.get("commence_time") or ""))
        )
    except (KeyError, TypeError, ValueError):
        return False


def _binding_for(
    store: SoccerStore,
    *,
    event_key: str,
    schedule_revision: int,
) -> dict[str, Any]:
    row = store.ops.get_item(
        Key={
            "PK": f"PUBLIC_PREDICTION_BINDING#{event_key}",
            "SK": (
                f"REV#{schedule_revision}#HORIZON#{PUBLIC_DECISION_HORIZON}#"
                "TARGET#result_1x2"
            ),
        },
        ConsistentRead=True,
    ).get("Item")
    return plain(row) if row else {}


def _prediction_binding_valid(
    prediction: Mapping[str, Any],
    binding: Mapping[str, Any],
    current_event: Mapping[str, Any],
) -> bool:
    """Recompute the complete immutable T10 publication binding."""
    try:
        revision = int(prediction["schedule_revision"])
        event_key = str(prediction["event_key"])
        cutoff = parse_utc(str(prediction["commence_time"])) - timedelta(
            minutes=PUBLICATION_CUTOFF_MINUTES
        )
        deadline = cutoff - timedelta(
            seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
        )
        capture_open = deadline - timedelta(
            seconds=FINAL_DECISION_CAPTURE_LEAD_SECONDS
        )
        lock_at = parse_utc(str(prediction["lock_at"]))
        binding_lock_at = parse_utc(str(binding["lock_at"]))
        source_observed_at = parse_utc(
            str(prediction["source_observed_at_max"])
        )
        created_at = parse_utc(str(prediction["created_at"]))
        bound_at = parse_utc(str(binding["bound_at"]))
        autonomy_updated_at = parse_utc(
            str(prediction["autonomy_updated_at"])
        )
        return bool(
            _same_schedule(prediction, current_event)
            and prediction.get("immutable") is True
            and prediction.get("model_authority") == "CHAMPION"
            and prediction.get("prediction_status") in {"PUBLISHED", "NO_PICK"}
            and prediction.get("horizon") == PUBLIC_DECISION_HORIZON
            and prediction.get("target") == "result_1x2"
            and prediction.get("lock_version")
            == LOCK_VERSION_BY_HORIZON[PUBLIC_DECISION_HORIZON]
            and str(prediction.get("publication_cutoff") or "")
            == iso_utc(cutoff)
            and str(prediction.get("decision_target_at") or "")
            == iso_utc(cutoff)
            and str(prediction.get("capture_opens_at") or "")
            == iso_utc(capture_open)
            and str(prediction.get("lock_commit_deadline") or "")
            == iso_utc(deadline)
            and str(prediction.get("commit_deadline") or "")
            == iso_utc(deadline)
            and float(prediction.get("commit_headroom_seconds"))
            == PUBLICATION_COMMIT_HEADROOM_SECONDS
            and capture_open <= lock_at <= deadline
            and source_observed_at <= lock_at <= created_at <= deadline
            and created_at == bound_at
            and binding_lock_at == lock_at
            and binding.get("entity_type")
            == "SOCCER_PUBLIC_PREDICTION_BINDING"
            and binding.get("binding_version")
            == PUBLIC_PREDICTION_BINDING_VERSION
            and binding.get("immutable") is True
            and str(binding.get("event_key") or "") == event_key
            and str(binding.get("event_id") or "")
            == str(prediction.get("event_id") or "")
            and str(binding.get("sport_key") or "")
            == str(prediction.get("sport_key") or "")
            and str(binding.get("commence_time") or "")
            == str(prediction.get("commence_time") or "")
            and int(binding.get("schedule_revision") or 0) == revision
            and str(binding.get("schedule_identity") or "")
            == str(prediction.get("schedule_identity") or "")
            and str(binding.get("horizon") or "")
            == PUBLIC_DECISION_HORIZON
            and str(binding.get("target") or "") == "result_1x2"
            and str(binding.get("lock_sk") or "")
            == lock_key(PUBLIC_DECISION_HORIZON, revision)
            and str(binding.get("lock_version") or "")
            == str(prediction.get("lock_version") or "")
            and str(binding.get("lock_at") or "")
            == str(prediction.get("lock_at") or "")
            and str(binding.get("decision_target_at") or "")
            == str(prediction.get("decision_target_at") or "")
            and str(binding.get("capture_opens_at") or "")
            == str(prediction.get("capture_opens_at") or "")
            and str(binding.get("lock_commit_deadline") or "")
            == str(prediction.get("lock_commit_deadline") or "")
            and str(binding.get("source_observed_at_max") or "")
            == str(prediction.get("source_observed_at_max") or "")
            and str(binding.get("feature_hash") or "")
            == str(prediction.get("feature_hash") or "")
            and bool(str(prediction.get("feature_hash") or ""))
            and str(binding.get("coverage_certificate_version") or "")
            == str(prediction.get("coverage_certificate_version") or "")
            and bool(str(prediction.get("coverage_certificate_version") or ""))
            and str(binding.get("coverage_certificate_digest") or "")
            == str(prediction.get("coverage_certificate_digest") or "")
            and bool(str(prediction.get("coverage_certificate_digest") or ""))
            and str(binding.get("coverage_plan_digest") or "")
            == str(prediction.get("coverage_plan_digest") or "")
            and bool(str(prediction.get("coverage_plan_digest") or ""))
            and str(binding.get("model_digest") or "")
            == str(prediction.get("model_digest") or "")
            and str(binding.get("publication_cutoff") or "")
            == iso_utc(cutoff)
            and str(binding.get("commit_deadline") or "")
            == iso_utc(deadline)
            and float(binding.get("commit_headroom_seconds"))
            == PUBLICATION_COMMIT_HEADROOM_SECONDS
            and str(binding.get("autonomy_updated_at") or "")
            == str(prediction.get("autonomy_updated_at") or "")
            and int(binding.get("autonomy_updated_at_epoch_ms") or 0)
            == int(prediction.get("autonomy_updated_at_epoch_ms") or 0)
            == int(autonomy_updated_at.timestamp() * 1000)
            and int(binding.get("event_metadata_revision") or 0)
            == int(prediction.get("event_metadata_revision") or 0)
            > 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def prediction_and_training_health(
    store: SoccerStore,
    *,
    observed: datetime | None = None,
) -> dict[str, Any]:
    observed = observed or now_utc()
    events, events_truncated = _scan(store.events)
    locks, locks_truncated = _scan(store.locks)
    settlement_rows, settlements_truncated = _scan(store.settlements)
    predictions, predictions_truncated = _scan(store.predictions)
    conflicted_events, conflicts_truncated = _conflicted_events(store)

    current_events = {
        str(row.get("event_key") or row.get("PK") or ""): row
        for row in events
        if row.get("entity_type") == "SOCCER_EVENT"
        and row.get("completed") is not True
        and row.get("event_key")
    }
    lock_rows = {
        (str(row.get("PK") or row.get("event_key") or ""), str(row.get("SK") or "")): row
        for row in locks
        if row.get("entity_type") == "SOCCER_FROZEN_FEATURE_LOCK"
    }

    due_events = 0
    open_capture_events = 0
    t10_locks = 0
    missing_t10_locks = 0
    missed_t10_locks = 0
    late_discovered_due_events = 0
    invalid_t10_locks = 0
    missing_sample: list[str] = []
    invalid_sample: list[str] = []
    for event_key, event in current_events.items():
        try:
            commence = parse_utc(str(event["commence_time"]))
            if not (
                observed - timedelta(hours=RECENT_DECISION_AUDIT_HOURS)
                <= commence
                <= observed + timedelta(hours=UPCOMING_DECISION_AUDIT_HOURS)
            ):
                continue
            cutoff = commence - timedelta(minutes=PUBLICATION_CUTOFF_MINUTES)
            deadline = cutoff - timedelta(
                seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
            )
            capture_open = deadline - timedelta(
                seconds=FINAL_DECISION_CAPTURE_LEAD_SECONDS
            )
            if observed < capture_open:
                continue
            due_events += 1
            if observed <= deadline:
                open_capture_events += 1
            revision = int(event.get("schedule_revision") or 0)
            key = (event_key, lock_key(PUBLIC_DECISION_HORIZON, revision))
            lock = lock_rows.get(key)
            first_seen = event.get("first_seen_at")
            discovered_late = bool(
                first_seen and parse_utc(str(first_seen)) > capture_open
            )
            if discovered_late:
                late_discovered_due_events += 1
            if not lock:
                missing_t10_locks += 1
                if observed > deadline:
                    missed_t10_locks += 1
                if len(missing_sample) < 20:
                    missing_sample.append(event_key)
                continue
            t10_locks += 1
            if not _same_schedule(lock, event) or not live_lock_coverage_provenance_valid(lock):
                invalid_t10_locks += 1
                if len(invalid_sample) < 20:
                    invalid_sample.append(event_key)
        except (KeyError, TypeError, ValueError):
            invalid_t10_locks += 1
            if len(invalid_sample) < 20:
                invalid_sample.append(event_key)

    public_rows = [
        row
        for row in predictions
        if row.get("model_authority") == "CHAMPION"
        and row.get("prediction_status") in {"PUBLISHED", "NO_PICK"}
        and row.get("immutable") is True
    ]
    legacy_t45_public_rows = sum(
        str(row.get("horizon") or "") == TRAINING_LOCK_HORIZON
        for row in public_rows
    )
    current_public_rows: dict[tuple[str, int], list[dict[str, Any]]] = {}
    public_prediction_after_cutoff = 0
    public_prediction_after_commit_deadline = 0
    public_binding_integrity_failures = 0
    for row in public_rows:
        if str(row.get("horizon") or "") != PUBLIC_DECISION_HORIZON:
            continue
        try:
            event_key = str(row["event_key"])
            revision = int(row["schedule_revision"])
            current_public_rows.setdefault((event_key, revision), []).append(row)
            cutoff = parse_utc(str(row["commence_time"])) - timedelta(
                minutes=PUBLICATION_CUTOFF_MINUTES
            )
            deadline = cutoff - timedelta(
                seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
            )
            created_at = parse_utc(str(row["created_at"]))
            if created_at > cutoff:
                public_prediction_after_cutoff += 1
            if created_at > deadline:
                public_prediction_after_commit_deadline += 1
            current = current_events.get(event_key) or store.get_event(event_key) or {}
            binding = _binding_for(
                store,
                event_key=event_key,
                schedule_revision=revision,
            )
            if not _prediction_binding_valid(row, binding, current):
                public_binding_integrity_failures += 1
        except (KeyError, TypeError, ValueError):
            public_binding_integrity_failures += 1
    duplicate_public_authorities = sum(
        max(0, len(rows) - 1) for rows in current_public_rows.values()
    )

    settlement_views = settlement_training_views(settlement_rows)
    validated_settlements = [
        row for row in settlement_views if settlement_training_evidence_valid(row)
    ]
    admissible_settlements = [
        row for row in validated_settlements if settlement_training_admissible(row)
    ]
    training_rows_ready = 0
    training_exclusion_reasons: dict[str, int] = {}
    invalid_existing_locks = 0
    nontraining_live_locks = 0
    duplicate_training_eligible_locks = 0
    for settlement in admissible_settlements:
        try:
            live_sk = lock_key(
                TRAINING_LOCK_HORIZON,
                int(settlement["schedule_revision"]),
            )
            historical_sk = historical_training_lock_key(
                int(settlement["schedule_revision"])
            )
            event_key = str(settlement["event_key"])
            live = lock_rows.get((event_key, live_sk))
            historical = lock_rows.get((event_key, historical_sk))
            locks = [lock for lock in (live, historical) if lock is not None]
            if not locks:
                training_exclusion_reasons["no_t45_lock"] = (
                    training_exclusion_reasons.get("no_t45_lock", 0) + 1
                )
                continue
            assessment = select_training_candidate(
                locks,
                settlement,
                conflicted=event_key in conflicted_events,
            )
            for reason, count in assessment["invalid_live_reasons"].items():
                nontraining_live_locks += int(count)
                key = f"live:{reason}"
                training_exclusion_reasons[key] = (
                    training_exclusion_reasons.get(key, 0) + int(count)
                )
            for reason, count in assessment[
                "invalid_historical_reasons"
            ].items():
                invalid_existing_locks += int(count)
                key = f"historical:{reason}"
                training_exclusion_reasons[key] = (
                    training_exclusion_reasons.get(key, 0) + int(count)
                )
            duplicate_count = int(assessment["duplicate_eligible_locks"])
            if duplicate_count:
                duplicate_training_eligible_locks += duplicate_count
                invalid_existing_locks += duplicate_count
                training_exclusion_reasons[
                    "duplicate_training_authority"
                ] = (
                    training_exclusion_reasons.get(
                        "duplicate_training_authority", 0
                    )
                    + duplicate_count
                )
                continue
            candidate = assessment["candidate"]
            if candidate is not None:
                training_rows_ready += 1
        except (KeyError, TypeError, ValueError):
            invalid_existing_locks += 1
            training_exclusion_reasons["invalid"] = (
                training_exclusion_reasons.get("invalid", 0) + 1
            )

    certificates = [
        row
        for row in settlement_rows
        if row.get("entity_type")
        == "SOCCER_SETTLEMENT_ADMISSIBILITY_CERTIFICATE"
    ]
    if not admissible_settlements:
        training_state = "AWAITING_ADMISSIBLE_SETTLEMENTS"
    elif training_rows_ready == 0:
        training_state = "BLOCKED_NO_VALID_T45_EVIDENCE"
    elif training_rows_ready < len(admissible_settlements):
        training_state = "CONVERTING_ADMISSIBLE_SETTLEMENTS"
    else:
        training_state = "CURRENT"

    integrity_failures = (
        invalid_t10_locks
        + public_prediction_after_commit_deadline
        + duplicate_public_authorities
        + public_binding_integrity_failures
        + invalid_existing_locks
    )
    availability_warnings = missing_t10_locks + max(
        0, len(admissible_settlements) - training_rows_ready
    )
    scan_truncated = bool(
        events_truncated
        or locks_truncated
        or settlements_truncated
        or predictions_truncated
        or conflicts_truncated
    )
    if scan_truncated:
        state = "INCOMPLETE_PROOF"
    elif integrity_failures:
        state = "DEGRADED_INTEGRITY"
    elif availability_warnings:
        state = "DEGRADED_AVAILABILITY"
    elif not admissible_settlements:
        state = "COLLECTING_LABELS"
    else:
        state = "HEALTHY"

    return {
        "contract_version": HEALTH_CONTRACT_VERSION,
        "observed_at": iso_utc(observed),
        "state": state,
        "healthy": integrity_failures == 0 and not scan_truncated,
        "proof_complete": not scan_truncated,
        "integrity_failures": integrity_failures,
        "availability_warnings": availability_warnings,
        "scan_truncated": scan_truncated,
        "t10_decisions": {
            "due_events": due_events,
            "open_capture_events": open_capture_events,
            "valid_or_present_locks": t10_locks - invalid_t10_locks,
            "missing_locks": missing_t10_locks,
            "missed_locks": missed_t10_locks,
            "late_discovered_due_events": late_discovered_due_events,
            "invalid_locks": invalid_t10_locks,
            "missing_sample": missing_sample,
            "invalid_sample": invalid_sample,
            "capture_lead_seconds": FINAL_DECISION_CAPTURE_LEAD_SECONDS,
            "publication_cutoff_minutes": PUBLICATION_CUTOFF_MINUTES,
            "commit_headroom_seconds": PUBLICATION_COMMIT_HEADROOM_SECONDS,
        },
        "public_authority": {
            "t10_public_rows": sum(len(rows) for rows in current_public_rows.values()),
            "legacy_t45_rows_suppressed": legacy_t45_public_rows,
            "duplicate_public_authorities": duplicate_public_authorities,
            "public_prediction_after_cutoff": public_prediction_after_cutoff,
            "public_prediction_after_commit_deadline": (
                public_prediction_after_commit_deadline
            ),
            "binding_integrity_failures": public_binding_integrity_failures,
        },
        "training": {
            "validated_final_score_rows": len(validated_settlements),
            "admissible_final_score_rows": len(admissible_settlements),
            "admissibility_certificates": len(certificates),
            "training_rows_ready": training_rows_ready,
            "conversion_backlog": max(
                0, len(admissible_settlements) - training_rows_ready
            ),
            "invalid_existing_locks": invalid_existing_locks,
            "nontraining_live_locks": nontraining_live_locks,
            "duplicate_training_eligible_locks": (
                duplicate_training_eligible_locks
            ),
            "exclusion_reasons": training_exclusion_reasons,
            "state": training_state,
            "latest_certificate_at": max(
                (str(row.get("observed_at") or "") for row in certificates),
                default=None,
            ),
        },
    }
