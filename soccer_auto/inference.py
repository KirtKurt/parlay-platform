"""Dual immutable soccer locks and champion/challenger inference.

T45 is retained as the leakage-safe training/audit horizon.  T10 is the only
public-decision horizon.  A T10 lock is captured at-or-before the publication
deadline and records its exact evidence cutoff, so late data can never repaint
the final decision.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from .canonical import (
    digest,
    iso_utc,
    merge_event_payloads,
    parse_utc,
    schedule_identity,
)
from .config import (
    FINAL_DECISION_CAPTURE_LEAD_SECONDS,
    LOCK_MINUTES_BY_HORIZON,
    LOCK_VERSION_BY_HORIZON,
    PUBLIC_DECISION_HORIZON,
    PUBLIC_PREDICTION_BINDING_VERSION,
    PUBLICATION_COMMIT_HEADROOM_SECONDS,
    PUBLICATION_CUTOFF_MINUTES,
    TRAINING_LOCK_HORIZON,
)
from .market_features import FEATURE_SCHEMA_VERSION, compile_features
from .model import CLASSES, ResidualSoftmaxModel
from .storage import (
    COVERAGE_CERTIFICATE_VERSION,
    SoccerStore,
    now_utc,
    plain,
)


# Backward-compatible aliases are intentionally retained for modules/tests that
# refer to the original training lock constant directly.
LOCK_VERSION = LOCK_VERSION_BY_HORIZON[TRAINING_LOCK_HORIZON]
PUBLIC_BINDING_VERSION = PUBLIC_PREDICTION_BINDING_VERSION
MIN_BOOKMAKERS = int(os.getenv("SOCCER_AUTO_MIN_BOOKMAKERS", "3"))
PUBLISH_CONFIDENCE = float(os.getenv("SOCCER_AUTO_PUBLISH_CONFIDENCE", "0.50"))
AUTONOMY_STATE_MAX_AGE_MINUTES = 30
AUTONOMY_STATE_MAX_FUTURE_SKEW_MINUTES = 2


def _normalized_horizon(value: Any) -> str:
    horizon = str(value or "").strip().upper()
    if horizon not in LOCK_MINUTES_BY_HORIZON:
        raise ValueError(f"unsupported soccer lock horizon: {horizon or '<empty>'}")
    return horizon


def _lock_horizon(lock: Mapping[str, Any]) -> str:
    """Return the explicit horizon, with one narrow legacy-T45 fallback."""
    explicit = str(lock.get("horizon") or "").strip().upper()
    if explicit:
        return _normalized_horizon(explicit)
    key = str(lock.get("SK") or "")
    version = str(lock.get("lock_version") or "")
    if key.startswith("LOCK#T45#") and version == LOCK_VERSION:
        return TRAINING_LOCK_HORIZON
    raise ValueError("lock horizon is missing or inconsistent")


def lock_key(
    horizon: str,
    schedule_revision: int,
    target: str = "result_1x2",
) -> str:
    normalized = _normalized_horizon(horizon)
    return f"LOCK#{normalized}#REV#{int(schedule_revision)}#TARGET#{target}"


def _lock_timing(
    commence: datetime,
    *,
    horizon: str,
    observed: datetime,
) -> dict[str, datetime]:
    """Return immutable target/capture timing for one lock horizon."""
    normalized = _normalized_horizon(horizon)
    target_at = commence - timedelta(
        minutes=LOCK_MINUTES_BY_HORIZON[normalized]
    )
    if normalized == TRAINING_LOCK_HORIZON:
        return {
            "target_at": target_at,
            "capture_opens_at": target_at,
            "commit_deadline": target_at,
            "evidence_cutoff_at": target_at,
        }
    commit_deadline = target_at - timedelta(
        seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
    )
    capture_opens_at = commit_deadline - timedelta(
        seconds=FINAL_DECISION_CAPTURE_LEAD_SECONDS
    )
    # The final lock stores the exact pre-deadline evidence cutoff used by this
    # invocation.  It is never backdated to the nominal T10 target.
    evidence_cutoff_at = min(observed, commit_deadline)
    return {
        "target_at": target_at,
        "capture_opens_at": capture_opens_at,
        "commit_deadline": commit_deadline,
        "evidence_cutoff_at": evidence_cutoff_at,
    }


def _latest_and_earliest_by_scope(slots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for row in slots:
        by_scope.setdefault(row["scope_hash"], []).append(row)
    latest = []
    earliest = []
    for rows in by_scope.values():
        ordered = sorted(rows, key=lambda row: (row["slot_start"], row["payload_sha256"]))
        earliest.append(ordered[0])
        latest.append(ordered[-1])
    return latest, earliest


def _merged_from_pointers(store: SoccerStore, pointers: list[dict[str, Any]]) -> dict[str, Any]:
    payloads = []
    for pointer in pointers:
        payload = store.read_json(pointer["raw_uri"])
        if digest(payload) != str(pointer.get("payload_sha256") or ""):
            raise ValueError("canonical snapshot payload digest mismatch")
        payloads.append(payload)
    return merge_event_payloads(payloads)


def _payload_pairs(payload: Mapping[str, Any]) -> set[str]:
    return {
        f"{book.get('key')}|{market.get('key')}"
        for book in payload.get("bookmakers") or []
        if book.get("key")
        for market in book.get("markets") or []
        if market.get("key")
    }


def _certified_cohort(
    store: SoccerStore,
    *,
    event_key: str,
    schedule_revision: int,
    schedule_identity_value: str,
    lock_at: str,
    certificate: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Load only canonical slots bound to one completed coverage plan."""
    plan_digest = str(certificate.get("plan_digest") or "")
    plan_observed_at = str(certificate.get("plan_observed_at") or "")
    required_pairs = {
        str(pair) for pair in certificate.get("required_pairs") or [] if pair
    }
    slots = store.canonical_slots_before(
        event_key,
        lock_at,
        schedule_revision=schedule_revision,
        schedule_identity=schedule_identity_value,
        coverage_plan_digest=plan_digest,
        coverage_plan_observed_at=plan_observed_at,
    )
    if not slots:
        return None
    completed_at = parse_utc(str(certificate.get("completed_at") or ""))
    if any(
        str(row.get("coverage_plan_digest") or "") != plan_digest
        or str(row.get("coverage_plan_observed_at") or "")
        != plan_observed_at
        or parse_utc(str(row.get("observed_at") or "")) > completed_at
        for row in slots
    ):
        return None
    pointers, _ = _latest_and_earliest_by_scope(slots)
    reported_pairs = {
        str(pair)
        for row in pointers
        for pair in row.get("pair_keys_returned") or []
        if pair
    }
    if not required_pairs or not required_pairs <= reported_pairs:
        return None
    payload = _merged_from_pointers(store, pointers)
    if not required_pairs <= _payload_pairs(payload):
        return None
    return pointers, payload


def build_frozen_lock(
    store: SoccerStore,
    event: Mapping[str, Any],
    *,
    observed_at: str,
    horizon: str = TRAINING_LOCK_HORIZON,
) -> dict[str, Any]:
    event_key = str(event["event_key"])
    schedule_revision = int(event.get("schedule_revision") or 0)
    if schedule_revision <= 0:
        raise ValueError("a positive schedule_revision is required for a frozen lock")
    commence = parse_utc(str(event["commence_time"]))
    normalized_horizon = _normalized_horizon(horizon)
    observed = parse_utc(observed_at)
    timing = _lock_timing(
        commence,
        horizon=normalized_horizon,
        observed=observed,
    )
    target_at = timing["target_at"]
    capture_opens_at = timing["capture_opens_at"]
    commit_deadline = timing["commit_deadline"]
    lock_at = timing["evidence_cutoff_at"]
    event_schedule_identity = str(
        event.get("schedule_identity") or schedule_identity(event)
    )
    base = {
        "PK": event_key,
        "SK": lock_key(normalized_horizon, schedule_revision),
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        "lock_version": LOCK_VERSION_BY_HORIZON[normalized_horizon],
        "event_key": event_key,
        "event_id": event["event_id"],
        "sport_key": event["sport_key"],
        "commence_time": iso_utc(commence),
        "schedule_revision": schedule_revision,
        "schedule_identity": event_schedule_identity,
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "target": "result_1x2",
        "horizon": normalized_horizon,
        "minutes_before_start": LOCK_MINUTES_BY_HORIZON[
            normalized_horizon
        ],
        "decision_target_at": iso_utc(target_at),
        "capture_opens_at": iso_utc(capture_opens_at),
        "lock_commit_deadline": iso_utc(commit_deadline),
        "lock_at": iso_utc(lock_at),
        "created_at": iso_utc(observed),
        "labels": None,
        "immutable": True,
    }
    if observed < capture_opens_at:
        return {**base, "write_ready": False, "reason": "LOCK_NOT_DUE"}
    if (
        normalized_horizon == PUBLIC_DECISION_HORIZON
        and observed > commit_deadline
    ):
        return {
            **base,
            "write_ready": False,
            "reason": "T10_FINAL_DECISION_WINDOW_CLOSED",
        }
    certificates = store.coverage_certificates_before(
        event_key,
        iso_utc(lock_at),
        schedule_revision=schedule_revision,
        schedule_identity=event_schedule_identity,
    )
    if not certificates:
        return {
            **base,
            "write_ready": False,
            "reason": "COMPLETE_PRELOCK_COVERAGE_CERTIFICATE_UNAVAILABLE",
        }
    cohort_cache: dict[
        tuple[str, str, str, str],
        tuple[list[dict[str, Any]], dict[str, Any]] | None,
    ] = {}

    def cohort_for(
        certificate: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        certificate_identity = (
            str(certificate.get("certificate_digest") or ""),
            str(certificate.get("plan_digest") or ""),
            str(certificate.get("plan_observed_at") or ""),
            str(certificate.get("completed_at") or ""),
        )
        if certificate_identity not in cohort_cache:
            cohort_cache[certificate_identity] = _certified_cohort(
                store,
                event_key=event_key,
                schedule_revision=schedule_revision,
                schedule_identity_value=event_schedule_identity,
                lock_at=iso_utc(lock_at),
                certificate=certificate,
            )
        return cohort_cache[certificate_identity]

    latest_certificate: Mapping[str, Any] | None = None
    latest_cohort: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
    for certificate in certificates:
        latest_cohort = cohort_for(certificate)
        if latest_cohort:
            latest_certificate = certificate
            break
    if latest_certificate is None or latest_cohort is None:
        return {
            **base,
            "write_ready": False,
            "reason": "NO_FINALIZED_CERTIFIED_PRELOCK_CANONICAL_COHORT",
        }
    baseline_certificate = latest_certificate
    baseline_cohort = latest_cohort
    for certificate in reversed(certificates):
        if str(certificate.get("plan_digest") or "") == str(
            latest_certificate.get("plan_digest") or ""
        ):
            continue
        candidate = cohort_for(certificate)
        if candidate:
            baseline_certificate = certificate
            baseline_cohort = candidate
            break
    latest_pointers, latest = latest_cohort
    baseline_pointers, earliest = baseline_cohort
    try:
        features = compile_features(
            latest,
            earliest=earliest,
            hours_to_start=(commence - lock_at).total_seconds() / 3600.0,
        )
    except Exception as exc:
        return {
            **base,
            "write_ready": True,
            "training_eligible": False,
            "prediction_eligible": False,
            "exclusion_reasons": ["FEATURE_COMPILATION_FAILED"],
            "failure_detail": str(exc),
            "source_slot_ids": [row["SK"] for row in latest_pointers],
            "source_payload_hashes": [row["payload_sha256"] for row in latest_pointers],
        }
    exclusion_reasons = []
    if int(features["book_count"]) < MIN_BOOKMAKERS:
        exclusion_reasons.append("INSUFFICIENT_THREE_WAY_BOOKMAKER_COVERAGE")
    source_hashes = [row["payload_sha256"] for row in latest_pointers]
    feature_hash = digest(
        {
            "event_key": event_key,
            "schedule_revision": schedule_revision,
            "lock_at": iso_utc(lock_at),
            "coverage_certificate_digest": latest_certificate[
                "certificate_digest"
            ],
            "coverage_plan_digest": latest_certificate["plan_digest"],
            "coverage_plan_observed_at": latest_certificate[
                "plan_observed_at"
            ],
            "coverage_completed_at": latest_certificate["completed_at"],
            "coverage_required_pairs": sorted(
                latest_certificate.get("required_pairs") or []
            ),
            "coverage_probe_pairs": sorted(
                latest_certificate.get("probe_pairs") or []
            ),
            "movement_baseline_certificate_digest": baseline_certificate[
                "certificate_digest"
            ],
            "movement_baseline_plan_digest": baseline_certificate[
                "plan_digest"
            ],
            "movement_baseline_plan_observed_at": baseline_certificate[
                "plan_observed_at"
            ],
            "source_slot_ids": [row["SK"] for row in latest_pointers],
            "source_hashes": source_hashes,
            "source_raw_uris": [row["raw_uri"] for row in latest_pointers],
            "source_observed_at_max": max(
                row["observed_at"] for row in latest_pointers
            ),
            "movement_baseline_source_slot_ids": [
                row["SK"] for row in baseline_pointers
            ],
            "movement_baseline_source_hashes": [
                row["payload_sha256"] for row in baseline_pointers
            ],
            "movement_baseline_source_raw_uris": [
                row["raw_uri"] for row in baseline_pointers
            ],
            "movement_baseline_source_observed_at_max": max(
                row["observed_at"] for row in baseline_pointers
            ),
            "features": features,
        }
    )
    return {
        **base,
        "write_ready": True,
        "training_eligible": bool(
            normalized_horizon == TRAINING_LOCK_HORIZON
            and not exclusion_reasons
        ),
        "prediction_eligible": bool(
            normalized_horizon == PUBLIC_DECISION_HORIZON
            and not exclusion_reasons
        ),
        "evidence_lead_seconds": max(
            0,
            int((target_at - lock_at).total_seconds()),
        ),
        "exclusion_reasons": exclusion_reasons,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_hash": feature_hash,
        "frozen_features": features,
        "coverage_certificate_version": COVERAGE_CERTIFICATE_VERSION,
        "coverage_certificate_digest": latest_certificate[
            "certificate_digest"
        ],
        "coverage_plan_digest": latest_certificate["plan_digest"],
        "coverage_plan_observed_at": latest_certificate[
            "plan_observed_at"
        ],
        "coverage_completed_at": latest_certificate["completed_at"],
        "coverage_required_pairs": sorted(
            latest_certificate.get("required_pairs") or []
        ),
        "coverage_required_pair_count": len(
            latest_certificate.get("required_pairs") or []
        ),
        "coverage_required_pair_digest": digest(
            sorted(latest_certificate.get("required_pairs") or [])
        ),
        "coverage_probe_pairs": sorted(
            latest_certificate.get("probe_pairs") or []
        ),
        "coverage_probe_pair_count": len(
            latest_certificate.get("probe_pairs") or []
        ),
        "coverage_probe_pair_digest": digest(
            sorted(latest_certificate.get("probe_pairs") or [])
        ),
        "coverage_completed_before_lock": parse_utc(
            str(latest_certificate["completed_at"])
        )
        <= lock_at,
        "movement_baseline_certificate_digest": baseline_certificate[
            "certificate_digest"
        ],
        "movement_baseline_plan_digest": baseline_certificate[
            "plan_digest"
        ],
        "movement_baseline_plan_observed_at": baseline_certificate[
            "plan_observed_at"
        ],
        "movement_baseline_distinct": (
            baseline_certificate["plan_digest"]
            != latest_certificate["plan_digest"]
        ),
        "movement_baseline_limitation": (
            None
            if baseline_certificate["plan_digest"]
            != latest_certificate["plan_digest"]
            else "NO_DISTINCT_EARLIER_CERTIFIED_PLAN"
        ),
        "source_slot_ids": [row["SK"] for row in latest_pointers],
        "source_payload_hashes": source_hashes,
        "source_raw_uris": [row["raw_uri"] for row in latest_pointers],
        "movement_baseline_source_slot_ids": [
            row["SK"] for row in baseline_pointers
        ],
        "movement_baseline_source_payload_hashes": [
            row["payload_sha256"] for row in baseline_pointers
        ],
        "movement_baseline_source_raw_uris": [
            row["raw_uri"] for row in baseline_pointers
        ],
        "movement_baseline_source_observed_at_max": max(
            row["observed_at"] for row in baseline_pointers
        ),
        "movement_baseline_source_observed_before_lock": all(
            parse_utc(row["observed_at"]) <= lock_at
            for row in baseline_pointers
        ),
        "source_observed_at_max": max(row["observed_at"] for row in latest_pointers),
        "source_observed_before_lock": all(parse_utc(row["observed_at"]) <= lock_at for row in latest_pointers),
    }


def live_lock_coverage_provenance_valid(lock: Mapping[str, Any]) -> bool:
    """Recompute the immutable live-lock binding before use.

    Legacy T45 v2 rows remain valid for training.  T10 rows must additionally
    prove that the exact evidence cutoff was inside the bounded final-decision
    capture window and no later than the atomic publication deadline.
    """
    try:
        horizon = _lock_horizon(lock)
        if lock.get("lock_version") != LOCK_VERSION_BY_HORIZON[horizon]:
            return False
        if (
            lock.get("coverage_certificate_version")
            != COVERAGE_CERTIFICATE_VERSION
        ):
            return False
        commence = parse_utc(str(lock["commence_time"]))
        lock_at = parse_utc(str(lock["lock_at"]))
        completed_at = parse_utc(str(lock["coverage_completed_at"]))
        plan_observed_at = parse_utc(
            str(lock["coverage_plan_observed_at"])
        )
        baseline_plan_observed_at = parse_utc(
            str(lock["movement_baseline_plan_observed_at"])
        )
        source_observed_at = parse_utc(str(lock["source_observed_at_max"]))
        baseline_observed_at = parse_utc(
            str(lock["movement_baseline_source_observed_at_max"])
        )
        expected_target_at = commence - timedelta(
            minutes=LOCK_MINUTES_BY_HORIZON[horizon]
        )
        target_at = parse_utc(
            str(lock.get("decision_target_at") or iso_utc(expected_target_at))
        )
        if horizon == TRAINING_LOCK_HORIZON:
            capture_opens_at = parse_utc(
                str(lock.get("capture_opens_at") or iso_utc(expected_target_at))
            )
            commit_deadline = parse_utc(
                str(lock.get("lock_commit_deadline") or iso_utc(expected_target_at))
            )
            timing_valid = bool(
                target_at == expected_target_at
                and capture_opens_at == expected_target_at
                and commit_deadline == expected_target_at
                and lock_at == expected_target_at
                and (
                    not lock.get("horizon")
                    or str(lock.get("horizon") or "").upper()
                    == TRAINING_LOCK_HORIZON
                )
                and (
                    lock.get("minutes_before_start") is None
                    or int(lock.get("minutes_before_start") or 0)
                    == LOCK_MINUTES_BY_HORIZON[TRAINING_LOCK_HORIZON]
                )
            )
        else:
            expected_commit_deadline = expected_target_at - timedelta(
                seconds=PUBLICATION_COMMIT_HEADROOM_SECONDS
            )
            expected_capture_opens_at = expected_commit_deadline - timedelta(
                seconds=FINAL_DECISION_CAPTURE_LEAD_SECONDS
            )
            capture_opens_at = parse_utc(str(lock["capture_opens_at"]))
            commit_deadline = parse_utc(str(lock["lock_commit_deadline"]))
            created_at = parse_utc(str(lock["created_at"]))
            timing_valid = bool(
                str(lock.get("horizon") or "").upper()
                == PUBLIC_DECISION_HORIZON
                and int(lock.get("minutes_before_start") or 0)
                == LOCK_MINUTES_BY_HORIZON[PUBLIC_DECISION_HORIZON]
                and target_at == expected_target_at
                and capture_opens_at == expected_capture_opens_at
                and commit_deadline == expected_commit_deadline
                and capture_opens_at <= lock_at <= commit_deadline
                and created_at == lock_at
                and lock.get("training_eligible") is False
                and lock.get("prediction_eligible") is True
            )
        if not timing_valid:
            return False

        required_values = list(lock.get("coverage_required_pairs") or [])
        probe_values = list(lock.get("coverage_probe_pairs") or [])
        required = {str(value) for value in required_values if value}
        probe = {str(value) for value in probe_values if value}
        source_ids = list(lock.get("source_slot_ids") or [])
        source_hashes = list(lock.get("source_payload_hashes") or [])
        source_uris = list(lock.get("source_raw_uris") or [])
        baseline_ids = list(
            lock.get("movement_baseline_source_slot_ids") or []
        )
        baseline_hashes = list(
            lock.get("movement_baseline_source_payload_hashes") or []
        )
        baseline_uris = list(
            lock.get("movement_baseline_source_raw_uris") or []
        )
        certificate_digest = str(
            lock.get("coverage_certificate_digest") or ""
        )
        baseline_certificate_digest = str(
            lock.get("movement_baseline_certificate_digest") or ""
        )
        expected_feature_hash = digest(
            {
                "event_key": lock["event_key"],
                "schedule_revision": int(lock["schedule_revision"]),
                "lock_at": iso_utc(lock_at),
                "coverage_certificate_digest": certificate_digest,
                "coverage_plan_digest": lock["coverage_plan_digest"],
                "coverage_plan_observed_at": lock[
                    "coverage_plan_observed_at"
                ],
                "coverage_completed_at": lock["coverage_completed_at"],
                "coverage_required_pairs": sorted(required),
                "coverage_probe_pairs": sorted(probe),
                "movement_baseline_certificate_digest": (
                    baseline_certificate_digest
                ),
                "movement_baseline_plan_digest": lock[
                    "movement_baseline_plan_digest"
                ],
                "movement_baseline_plan_observed_at": lock[
                    "movement_baseline_plan_observed_at"
                ],
                "source_slot_ids": source_ids,
                "source_hashes": source_hashes,
                "source_raw_uris": source_uris,
                "source_observed_at_max": iso_utc(source_observed_at),
                "movement_baseline_source_slot_ids": baseline_ids,
                "movement_baseline_source_hashes": baseline_hashes,
                "movement_baseline_source_raw_uris": baseline_uris,
                "movement_baseline_source_observed_at_max": iso_utc(
                    baseline_observed_at
                ),
                "features": lock["frozen_features"],
            }
        )
        baseline_distinct = bool(lock.get("movement_baseline_distinct"))

        def valid_sha256(value: Any) -> bool:
            text = str(value)
            return len(text) == 64 and int(text, 16) >= 0

        return bool(
            lock.get("entity_type") == "SOCCER_FROZEN_FEATURE_LOCK"
            and lock.get("immutable") is True
            and lock.get("labels") is None
            and str(lock.get("target") or "") == "result_1x2"
            and str(lock.get("PK") or "") == str(lock["event_key"])
            and str(lock.get("SK") or "")
            == lock_key(horizon, int(lock["schedule_revision"]))
            and str(lock.get("schedule_identity") or "")
            == schedule_identity(lock)
            and baseline_plan_observed_at <= plan_observed_at <= completed_at
            and completed_at <= lock_at
            and lock.get("coverage_completed_before_lock") is True
            and source_observed_at <= lock_at
            and lock.get("source_observed_before_lock") is True
            and baseline_observed_at <= lock_at
            and lock.get("movement_baseline_source_observed_before_lock")
            is True
            and valid_sha256(certificate_digest)
            and valid_sha256(baseline_certificate_digest)
            and bool(str(lock.get("coverage_plan_digest") or ""))
            and bool(str(lock.get("coverage_plan_observed_at") or ""))
            and len(required_values) == len(required)
            and len(probe_values) == len(probe)
            and bool(required)
            and not (required & probe)
            and all("|" in pair for pair in required | probe)
            and int(lock.get("coverage_required_pair_count") or 0)
            == len(required)
            and str(lock.get("coverage_required_pair_digest") or "")
            == digest(sorted(required))
            and int(lock.get("coverage_probe_pair_count") or 0)
            == len(probe)
            and str(lock.get("coverage_probe_pair_digest") or "")
            == digest(sorted(probe))
            and len(source_ids) == len(source_hashes) == len(source_uris) > 0
            and len(baseline_ids)
            == len(baseline_hashes)
            == len(baseline_uris)
            > 0
            and all(str(uri).startswith("s3://") for uri in source_uris)
            and all(str(uri).startswith("s3://") for uri in baseline_uris)
            and all(valid_sha256(value) for value in source_hashes)
            and all(valid_sha256(value) for value in baseline_hashes)
            and (
                baseline_distinct
                == (
                    str(lock.get("movement_baseline_plan_digest") or "")
                    != str(lock.get("coverage_plan_digest") or "")
                )
            )
            and (
                baseline_distinct
                or baseline_certificate_digest == certificate_digest
            )
            and str(lock.get("feature_hash") or "") == expected_feature_hash
        )
    except (KeyError, TypeError, ValueError):
        return False


def _active_models(store: SoccerStore) -> list[dict[str, Any]]:
    rows = store.model_items()
    active: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("SK") == "CHAMPION":
            active[row["model_digest"]] = {**row, "authority_state": "CHAMPION"}
        elif row.get("authority_state") == "PROSPECTIVE_SHADOW":
            active.setdefault(row["model_digest"], row)
    return sorted(active.values(), key=lambda row: (row.get("authority_state") != "CHAMPION", row["model_digest"]))


def _load_model(store: SoccerStore, row: Mapping[str, Any]) -> ResidualSoftmaxModel:
    artifact = store.read_json(str(row["artifact_uri"]))
    model_payload = artifact.get("model") or artifact
    model = ResidualSoftmaxModel.from_dict(model_payload)
    if model_payload.get("model_digest") != row.get("model_digest"):
        raise ValueError("model registry and artifact digests disagree")
    return model


def _same_schedule(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        left_identity = str(left.get("schedule_identity") or schedule_identity(left))
        right_identity = str(right.get("schedule_identity") or schedule_identity(right))
        # A persisted digest must still agree with the fields carried beside it.
        # This prevents a partial metadata rewrite from preserving authority by
        # copying an old digest onto a new team or kickoff identity.
        if left.get("schedule_identity") and left_identity != schedule_identity(left):
            return False
        if right.get("schedule_identity") and right_identity != schedule_identity(right):
            return False
        return (
            int(left.get("schedule_revision") or 0) > 0
            and int(left.get("schedule_revision") or 0)
            == int(right.get("schedule_revision") or 0)
            and left_identity == right_identity
        )
    except (KeyError, TypeError, ValueError):
        return False


def _public_binding_key(lock: Mapping[str, Any]) -> dict[str, str]:
    horizon = _lock_horizon(lock)
    if horizon != PUBLIC_DECISION_HORIZON:
        raise ValueError("public binding requires the immutable T10 horizon")
    return {
        "PK": f"PUBLIC_PREDICTION_BINDING#{lock['event_key']}",
        "SK": (
            f"REV#{int(lock['schedule_revision'])}#HORIZON#{horizon}#"
            "TARGET#result_1x2"
        ),
    }


def _public_model_binding(
    lock: Mapping[str, Any],
    *,
    model_digest: str,
) -> dict[str, Any]:
    """Build the static binding written atomically with its prediction."""
    key = _public_binding_key(lock)
    horizon = _lock_horizon(lock)
    lock_identity = str(lock.get("schedule_identity") or schedule_identity(lock))
    return {
        **key,
        "entity_type": "SOCCER_PUBLIC_PREDICTION_BINDING",
        "binding_version": PUBLIC_BINDING_VERSION,
        "event_key": lock["event_key"],
        "event_id": lock["event_id"],
        "sport_key": lock["sport_key"],
        "commence_time": iso_utc(str(lock["commence_time"])),
        "schedule_revision": int(lock["schedule_revision"]),
        "schedule_identity": lock_identity,
        "horizon": horizon,
        "target": "result_1x2",
        "lock_sk": lock["SK"],
        "lock_version": lock["lock_version"],
        "lock_at": lock["lock_at"],
        "decision_target_at": lock["decision_target_at"],
        "capture_opens_at": lock["capture_opens_at"],
        "lock_commit_deadline": lock["lock_commit_deadline"],
        "source_observed_at_max": lock["source_observed_at_max"],
        "feature_hash": lock["feature_hash"],
        "coverage_certificate_version": lock[
            "coverage_certificate_version"
        ],
        "coverage_certificate_digest": lock[
            "coverage_certificate_digest"
        ],
        "coverage_plan_digest": lock["coverage_plan_digest"],
        "model_digest": model_digest,
        "immutable": True,
    }


def _champion_publish_permission(
    store: SoccerStore,
    *,
    observed: datetime,
) -> tuple[bool, str, dict[str, Any]]:
    """Read the health authority immediately before champion inference.

    A missing, unreadable, stale, degraded, or promotion-blocked state is a
    normal fail-closed condition. The champion row is left missing so a later
    freeze cycle can retry it after authority recovers.
    """
    try:
        state = store.ops.get_item(
            Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True
        ).get("Item")
        state = plain(state) if state else {}
    except Exception:
        return False, "AUTONOMY_STATE_UNAVAILABLE", {}
    authority_allowed = bool(
        state.get("authority") == "AUTHORITATIVE"
        and state.get("automatic_prediction_allowed") is True
        and not state.get("promotion_blocked")
    )
    if not authority_allowed:
        return False, "AUTONOMY_PUBLISH_NOT_ALLOWED", state
    try:
        updated_at = parse_utc(str(state["updated_at"]))
        updated_at_epoch_ms = int(state["updated_at_epoch_ms"])
    except (KeyError, TypeError, ValueError):
        return False, "AUTONOMY_STATE_STALE", state
    if updated_at_epoch_ms != int(updated_at.timestamp() * 1000):
        return False, "AUTONOMY_STATE_STALE", state
    age = observed - updated_at
    if age > timedelta(minutes=AUTONOMY_STATE_MAX_AGE_MINUTES) or age < -timedelta(
        minutes=AUTONOMY_STATE_MAX_FUTURE_SKEW_MINUTES
    ):
        return False, "AUTONOMY_STATE_STALE", state
    return True, "", state


def _deadline_block(
    model_row: Mapping[str, Any],
    *,
    authority: str,
    observed: datetime,
    publication_cutoff: datetime,
    commit_deadline: datetime,
) -> dict[str, Any] | None:
    if observed <= commit_deadline:
        return None
    return {
        "model_digest": model_row.get("model_digest"),
        "model_authority": authority,
        "reason": (
            "PUBLICATION_AFTER_T10_CUTOFF"
            if observed > publication_cutoff
            else "PUBLICATION_COMMIT_HEADROOM_EXCEEDED"
        ),
        "publication_cutoff": iso_utc(publication_cutoff),
        "commit_deadline": iso_utc(commit_deadline),
        "commit_headroom_seconds": PUBLICATION_COMMIT_HEADROOM_SECONDS,
        "observed_at": iso_utc(observed),
    }


def predict_lock(
    store: SoccerStore,
    lock: Mapping[str, Any],
    *,
    observed_at: str,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Run inference while treating only the wall clock as deadline authority.

    ``observed_at`` remains the freeze invocation's audit timestamp. It must
    never authorize a later prediction write, because model loading and other
    work can cross the T-10 boundary after the handler starts.
    """
    wall_clock = clock or now_utc
    try:
        horizon = _lock_horizon(lock)
    except ValueError:
        return {"models": 0, "predictions": 0, "reason": "LOCK_HORIZON_INVALID"}
    if horizon != PUBLIC_DECISION_HORIZON:
        return {
            "models": 0,
            "predictions": 0,
            "reason": "LOCK_NOT_FINAL_DECISION_HORIZON",
        }
    if not lock.get("prediction_eligible"):
        return {"models": 0, "predictions": 0, "reason": "LOCK_NOT_PREDICTION_ELIGIBLE"}
    if not live_lock_coverage_provenance_valid(lock):
        return {
            "models": 0,
            "predictions": 0,
            "reason": "LOCK_COVERAGE_CERTIFICATE_INVALID",
        }
    current_event = store.get_event(str(lock["event_key"]))
    if not current_event or not _same_schedule(lock, current_event):
        return {"models": 0, "predictions": 0, "reason": "STALE_SCHEDULE_IDENTITY"}
    features = lock["frozen_features"]
    schedule_revision = int(lock["schedule_revision"])
    lock_schedule_identity = str(
        lock.get("schedule_identity") or schedule_identity(lock)
    )
    publication_cutoff = parse_utc(str(lock["decision_target_at"]))
    commit_deadline = parse_utc(str(lock["lock_commit_deadline"]))
    written = 0
    failures = []
    blocked = []
    active_models = _active_models(store)
    for model_row in active_models:
        try:
            authority = model_row["authority_state"]
            inference_observed = wall_clock()
            deadline_block = _deadline_block(
                model_row,
                authority=authority,
                observed=inference_observed,
                publication_cutoff=publication_cutoff,
                commit_deadline=commit_deadline,
            )
            if deadline_block:
                blocked.append(deadline_block)
                continue
            if authority == "CHAMPION":
                publish_allowed, publish_block_reason, autonomy = (
                    _champion_publish_permission(
                        store,
                        observed=inference_observed,
                    )
                )
            else:
                publish_allowed, publish_block_reason, autonomy = True, "", {}
            if not publish_allowed:
                blocked.append(
                    {
                        "model_digest": model_row.get("model_digest"),
                        "reason": publish_block_reason,
                        "autonomy_authority": autonomy.get("authority"),
                    }
                )
                continue
            model = _load_model(store, model_row)
            if tuple(model.feature_names) != tuple(features["feature_names"]):
                raise ValueError("feature schema mismatch")
            probabilities = model.predict_proba(features["values"], features["market_prior"])
            winner_index = max(range(3), key=lambda index: probabilities[index])
            confidence = probabilities[winner_index]
            abstention_reasons = []
            if authority != "CHAMPION":
                abstention_reasons.append("CHALLENGER_SHADOW_ONLY")
            if confidence < PUBLISH_CONFIDENCE:
                abstention_reasons.append("CONFIDENCE_BELOW_PREDECLARED_THRESHOLD")
            if authority == "CHAMPION":
                # Close the schedule-check-to-write window as far as possible
                # before claiming public authority. The immutable binding also
                # carries the full identity and fails closed on a numeric
                # revision collision.
                latest_event = store.get_event(str(lock["event_key"]))
                if not latest_event or not _same_schedule(lock, latest_event):
                    blocked.append(
                        {
                            "model_digest": model_row.get("model_digest"),
                            "reason": "STALE_SCHEDULE_IDENTITY",
                        }
                    )
                    continue
                binding_observed = wall_clock()
                deadline_block = _deadline_block(
                    model_row,
                    authority=authority,
                    observed=binding_observed,
                    publication_cutoff=publication_cutoff,
                    commit_deadline=commit_deadline,
                )
                if deadline_block:
                    blocked.append(deadline_block)
                    continue
                publish_allowed, publish_block_reason, autonomy = (
                    _champion_publish_permission(
                        store,
                        observed=binding_observed,
                    )
                )
                if not publish_allowed:
                    blocked.append(
                        {
                            "model_digest": model_row.get("model_digest"),
                            "reason": publish_block_reason,
                            "autonomy_authority": autonomy.get("authority"),
                        }
                    )
                    continue
            status = (
                "PUBLISHED"
                if authority == "CHAMPION" and not abstention_reasons
                else "NO_PICK"
                if authority == "CHAMPION"
                else "SHADOW"
            )
            prediction = {
                "PK": lock["event_key"],
                "SK": (
                    f"PRED#{horizon}#REV#{schedule_revision}#TARGET#result_1x2#"
                    f"MODEL#{model_row['model_digest']}"
                ),
                "entity_type": "SOCCER_MODEL_PREDICTION",
                "event_key": lock["event_key"],
                "event_id": lock["event_id"],
                "sport_key": lock["sport_key"],
                "commence_time": lock["commence_time"],
                "schedule_revision": schedule_revision,
                "schedule_identity": lock_schedule_identity,
                "home_team": lock.get("home_team"),
                "away_team": lock.get("away_team"),
                "target": "result_1x2",
                "horizon": horizon,
                "lock_at": lock["lock_at"],
                "decision_target_at": lock["decision_target_at"],
                "capture_opens_at": lock["capture_opens_at"],
                "lock_commit_deadline": lock["lock_commit_deadline"],
                "source_observed_at_max": lock["source_observed_at_max"],
                "publication_cutoff": iso_utc(publication_cutoff),
                "commit_deadline": iso_utc(commit_deadline),
                "commit_headroom_seconds": PUBLICATION_COMMIT_HEADROOM_SECONDS,
                "feature_hash": lock["feature_hash"],
                "feature_schema_version": lock["feature_schema_version"],
                "lock_version": lock["lock_version"],
                "coverage_certificate_version": lock[
                    "coverage_certificate_version"
                ],
                "coverage_certificate_digest": lock[
                    "coverage_certificate_digest"
                ],
                "coverage_plan_digest": lock["coverage_plan_digest"],
                "model_digest": model_row["model_digest"],
                "model_authority": authority,
                "probabilities": {CLASSES[index]: probabilities[index] for index in range(3)},
                "market_prior": {CLASSES[index]: features["market_prior"][index] for index in range(3)},
                "selection": CLASSES[winner_index] if status == "PUBLISHED" else None,
                "highest_probability_outcome": CLASSES[winner_index],
                "confidence": confidence,
                "prediction_status": status,
                "abstention_reasons": abstention_reasons,
                "immutable": True,
                "GSI1PK": "SOCCER_PREDICTIONS",
                "GSI2PK": f"MODEL#{model_row['model_digest']}",
                "GSI2SK": (
                    f"{lock['commence_time']}#REV#{schedule_revision}#"
                    f"HORIZON#{horizon}#{lock['event_key']}"
                ),
            }
            binding = (
                _public_model_binding(
                    lock,
                    model_digest=str(model_row["model_digest"]),
                )
                if authority == "CHAMPION"
                else None
            )
            # This is deliberately the final clock read and deadline check,
            # immediately adjacent to the immutable write. Both champion and
            # shadow rows are forbidden after T-10, and their timestamps record
            # this actual write attempt rather than the handler's start time.
            write_observed = wall_clock()
            deadline_block = _deadline_block(
                model_row,
                authority=authority,
                observed=write_observed,
                publication_cutoff=publication_cutoff,
                commit_deadline=commit_deadline,
            )
            if deadline_block:
                blocked.append(deadline_block)
                continue
            prediction["created_at"] = iso_utc(write_observed)
            prediction["GSI1SK"] = (
                f"{lock['commence_time']}#REV#{schedule_revision}#"
                f"{prediction['created_at']}#{lock['event_key']}#"
                f"{model_row['model_digest']}"
            )
            if authority == "CHAMPION":
                assert binding is not None
                try:
                    event_metadata_revision = int(
                        latest_event["metadata_revision"]
                    )
                    autonomy_updated_at_epoch_ms = int(
                        autonomy["updated_at_epoch_ms"]
                    )
                    if event_metadata_revision <= 0:
                        raise ValueError("event metadata revision is not positive")
                except (KeyError, TypeError, ValueError):
                    blocked.append(
                        {
                            "model_digest": model_row.get("model_digest"),
                            "reason": "PUBLICATION_AUTHORITY_REVISION_UNAVAILABLE",
                        }
                    )
                    continue
                binding["bound_at"] = prediction["created_at"]
                binding["publication_cutoff"] = prediction[
                    "publication_cutoff"
                ]
                binding["commit_deadline"] = prediction["commit_deadline"]
                binding["commit_headroom_seconds"] = prediction[
                    "commit_headroom_seconds"
                ]
                authority_evidence = {
                    "autonomy_updated_at": str(autonomy["updated_at"]),
                    "autonomy_updated_at_epoch_ms": (
                        autonomy_updated_at_epoch_ms
                    ),
                    "event_metadata_revision": event_metadata_revision,
                }
                binding.update(authority_evidence)
                prediction.update(authority_evidence)
                prediction_written, binding_reason, existing_binding = (
                    store.put_public_prediction(
                        binding=binding,
                        prediction=prediction,
                    )
                )
                if binding_reason not in {
                    "PUBLIC_PREDICTION_WRITTEN",
                    "PUBLIC_PREDICTION_RECOVERED",
                    "PUBLIC_PREDICTION_ALREADY_WRITTEN",
                }:
                    blocked.append(
                        {
                            "model_digest": model_row.get("model_digest"),
                            "reason": binding_reason,
                            "bound_model_digest": existing_binding.get(
                                "model_digest"
                            ),
                        }
                    )
                    continue
                written += int(prediction_written)
            else:
                written += int(store.put_prediction(prediction))
        except Exception as exc:
            failures.append({"model_digest": model_row.get("model_digest"), "error": str(exc)})
    return {
        "models": len(active_models),
        "predictions": written,
        "failures": failures,
        "blocked": blocked,
    }


def freeze_handler(event: Mapping[str, Any] | None, context: Any) -> dict[str, Any]:
    store = SoccerStore()
    observed = now_utc()
    observed_at = iso_utc(observed)
    events = store.active_events_between(
        iso_utc(observed),
        iso_utc(observed + timedelta(minutes=50)),
    )
    horizons = (TRAINING_LOCK_HORIZON, PUBLIC_DECISION_HORIZON)
    created_by_horizon = {horizon: 0 for horizon in horizons}
    retried_by_horizon = {horizon: 0 for horizon in horizons}
    blocked_by_horizon = {horizon: 0 for horizon in horizons}
    not_due_by_horizon = {horizon: 0 for horizon in horizons}
    reason_counts: dict[str, int] = {}
    predictions = 0
    publish_blocked = 0
    final_decision_window_misses = 0
    failures: list[dict[str, Any]] = []

    for row in events:
        try:
            schedule_revision = int(row.get("schedule_revision") or 0)
            if schedule_revision <= 0:
                raise ValueError("event is missing a positive schedule_revision")
            for horizon in horizons:
                lock = store.get_lock(
                    row["event_key"],
                    schedule_revision=schedule_revision,
                    horizon=horizon,
                )
                if lock:
                    retried_by_horizon[horizon] += 1
                else:
                    lock = build_frozen_lock(
                        store,
                        row,
                        observed_at=observed_at,
                        horizon=horizon,
                    )
                    if not lock.pop("write_ready", False):
                        reason = str(lock.get("reason") or "LOCK_NOT_READY")
                        reason_counts[reason] = reason_counts.get(reason, 0) + 1
                        not_due_by_horizon[horizon] += 1
                        if (
                            horizon == PUBLIC_DECISION_HORIZON
                            and reason == "T10_FINAL_DECISION_WINDOW_CLOSED"
                        ):
                            final_decision_window_misses += 1
                        continue
                    if not store.put_lock(lock):
                        # A concurrent freeze may have won the immutable write.
                        # Read that exact horizon/revision rather than creating a
                        # second authority or waiting for another schedule tick.
                        lock = store.get_lock(
                            row["event_key"],
                            schedule_revision=schedule_revision,
                            horizon=horizon,
                        )
                        if not lock:
                            continue
                        retried_by_horizon[horizon] += 1
                    else:
                        created_by_horizon[horizon] += 1
                        eligible_field = (
                            "training_eligible"
                            if horizon == TRAINING_LOCK_HORIZON
                            else "prediction_eligible"
                        )
                        if not lock.get(eligible_field):
                            blocked_by_horizon[horizon] += 1
                if horizon != PUBLIC_DECISION_HORIZON:
                    continue
                prediction_result = predict_lock(
                    store,
                    lock,
                    observed_at=observed_at,
                )
                predictions += int(prediction_result.get("predictions") or 0)
                publish_blocked += len(prediction_result.get("blocked") or [])
                failures.extend(prediction_result.get("failures") or [])
        except Exception as exc:
            failures.append(
                {"event_key": row.get("event_key"), "error": str(exc)}
            )

    return {
        "ok": not failures,
        "system": "soccer_auto",
        "events_considered": len(events),
        "training_horizon": TRAINING_LOCK_HORIZON,
        "public_decision_horizon": PUBLIC_DECISION_HORIZON,
        "locks_created": sum(created_by_horizon.values()),
        "locks_created_by_horizon": created_by_horizon,
        "locks_retried": sum(retried_by_horizon.values()),
        "locks_retried_by_horizon": retried_by_horizon,
        "locks_blocked": sum(blocked_by_horizon.values()),
        "locks_blocked_by_horizon": blocked_by_horizon,
        "not_due": sum(not_due_by_horizon.values()),
        "not_due_by_horizon": not_due_by_horizon,
        "lock_reason_counts": reason_counts,
        "final_decision_window_misses": final_decision_window_misses,
        "predictions_written": predictions,
        "champion_publish_blocked": publish_blocked,
        "failures": failures,
    }
