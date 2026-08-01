"""Resumable V7 feature rematerialization from immutable historical archives.

This migration never calls The Odds API. It rebuilds completed slate datasets from
already archived lock-bounded raw snapshots, writes content-addressed replacement
artifacts, and updates only the optimizer's dataset pointers and feature-version
metadata. Prior experiments and evaluated audit-window history remain intact.
"""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence

import mlb_historical_optimizer_handler as handler

VERSION = "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v1.3-fail-closed-pointer-reconciliation"
FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v7-odds-pattern-stack"
BATCH_SIZE = max(1, min(5, int(os.environ.get("MLB_HISTORICAL_REMATERIALIZE_SLATES_PER_RUN", "2"))))
ELIGIBLE_PHASES = {
    "DATA_RANGE_EXHAUSTED",
    "CANDIDATE_REJECTED",
    "OPTIMIZING",
    "BACKFILLING",
    "PAUSED_QUOTA",
    "REMATERIALIZING_FEATURES",
    "FEATURE_REMATERIALIZATION_BLOCKED",
    "WAITING_FOR_SETTLED_HORIZON",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quarantine_status(exc: Exception) -> str | None:
    message = str(exc).lower()
    if "too stale" in message:
        return "QUARANTINED_STALE"
    if "timestamp is after the request" in message:
        return "QUARANTINED_FUTURE_TIMESTAMP"
    if "timestamp is invalid" in message:
        return "QUARANTINED_INVALID_TIMESTAMP"
    if "payload data is not a list" in message or "non-object event" in message:
        return "QUARANTINED_MALFORMED_PAYLOAD"
    return None


def _rebuild_slate(day: str) -> Dict[str, Any]:
    finals, _ = handler._load_or_fetch_finals(day)
    official_count = int(finals.get("officialGameCount") or 0)
    if official_count <= 0:
        raise handler.OrchestrationError(f"rematerialization official slate missing:{day}")
    starts = [handler.optimizer._parse_dt(row.get("gameDate")) for row in finals.get("games") or []]
    starts = [value for value in starts if value is not None]
    if len(starts) != official_count:
        raise handler.OrchestrationError(f"rematerialization start-time mismatch:{day}")
    grid = handler.optimizer.build_snapshot_grid(day, starts)
    historical = []
    skipped = []
    for requested in grid.timestamps_utc:
        raw, pointer = handler._get_s3_json(handler._raw_key(day, requested))
        payload = raw.get("payload") if isinstance(raw, Mapping) and "payload" in raw else raw
        try:
            handler.optimizer.normalize_historical_snapshot(payload, requested)
        except handler.optimizer.HistoricalOptimizerError as exc:
            status = _quarantine_status(exc)
            if status is None:
                raise
            skipped.append(requested)
            historical.append(
                {
                    "requestedAtUtc": requested,
                    "status": status,
                    "usableForFeatures": False,
                    "reason": str(exc),
                    "sourceArtifact": pointer,
                }
            )
            continue
        historical.append(
            {
                "requestedAtUtc": requested,
                "status": "VALID",
                "usableForFeatures": True,
                "payload": payload,
            }
        )
    dataset = handler.optimizer.build_slate_dataset(
        day,
        finals.get("games") or [],
        historical,
        grid,
    )
    if dataset.get("completeSlate") is not True or float(dataset.get("exactSlateCoverage") or 0.0) < 1.0:
        raise handler.OrchestrationError(f"rematerialized slate incomplete:{day}")
    dataset["rematerializationVersion"] = VERSION
    dataset["featureDatasetVersion"] = FEATURE_DATASET_VERSION
    dataset["rematerializedAtUtc"] = _now_iso()
    dataset["sourceHistoricalOddsRequestsReused"] = len(historical)
    dataset["paidHistoricalCallsMade"] = 0
    dataset["skippedStaleArchivedSlots"] = skipped
    pointer = handler._put_immutable_json(
        handler._slate_key(day),
        dataset,
        record_type="mlb_historical_complete_slate",
    )
    return {
        "slateDateEt": day,
        "officialGameCount": int(dataset.get("officialGameCount") or 0),
        "eligibleGameCount": int(dataset.get("eligibleGameCount") or 0),
        "fingerprint": str(dataset.get("fingerprint") or ""),
        "artifact": pointer,
        "featureDatasetVersion": FEATURE_DATASET_VERSION,
        "rematerializationVersion": VERSION,
        "rematerializedAtUtc": dataset["rematerializedAtUtc"],
        "paidHistoricalCallsMade": 0,
        "quarantinedSnapshotCount": int(dataset.get("quarantinedSnapshotCount") or 0),
    }


def _migration_is_current(state: Mapping[str, Any]) -> bool:
    return str(state.get("featureRematerializationTargetDatasetVersion") or "") == FEATURE_DATASET_VERSION


def _first_mismatched_pointer(completed: Sequence[Mapping[str, Any]]) -> int:
    for index, row in enumerate(completed):
        if str(row.get("featureDatasetVersion") or "") != FEATURE_DATASET_VERSION:
            return index
    return len(completed)


def _state_is_fully_materialized(
    state: Mapping[str, Any], completed: Sequence[Mapping[str, Any]]
) -> bool:
    total = len(completed)
    return bool(
        total > 0
        and _migration_is_current(state)
        and state.get("featureDatasetVersion") == FEATURE_DATASET_VERSION
        and state.get("featureRematerializationComplete") is True
        and int(state.get("featureRematerializedSlateCount") or 0) == total
        and int(state.get("featureRematerializationTotalSlateCount") or 0) == total
        and _first_mismatched_pointer(completed) == total
        and not state.get("featureRematerializationErrors")
        and not state.get("lastError")
    )


def _begin_migration(state: Dict[str, Any], completed_count: int) -> None:
    prior_phase = str(state.get("phase") or "")
    if prior_phase not in {"REMATERIALIZING_FEATURES", "FEATURE_REMATERIALIZATION_BLOCKED"}:
        state["featureRematerializationPreviousPhase"] = prior_phase
    elif not state.get("featureRematerializationPreviousPhase"):
        state["featureRematerializationPreviousPhase"] = "BACKFILLING"
    state["featureRematerializationStartedAtUtc"] = _now_iso()
    state["featureRematerializationTargetDatasetVersion"] = FEATURE_DATASET_VERSION
    state["featureRematerializationTargetVersion"] = VERSION
    state["featureRematerializationCursor"] = 0
    state["featureRematerializedSlateCount"] = 0
    state["featureRematerializationTotalSlateCount"] = completed_count
    state["featureRematerializationComplete"] = False
    state["featureRematerializationErrors"] = []
    state["phase"] = "REMATERIALIZING_FEATURES"
    state["lastError"] = None


def run_once() -> Optional[Dict[str, Any]]:
    owner = f"feature-rematerialization-{uuid.uuid4()}"
    if not handler._acquire_lease(owner):
        return {"ok": True, "status": "LEASE_HELD", "rematerializing": True}
    try:
        state = handler._load_state()
        if not isinstance(state, dict):
            return None
        if state.get("phase") not in ELIGIBLE_PHASES:
            return None
        completed = sorted(
            [copy.deepcopy(row) for row in state.get("completedSlates") or [] if isinstance(row, Mapping)],
            key=lambda row: str(row.get("slateDateEt") or ""),
        )
        if not completed:
            return None
        if _state_is_fully_materialized(state, completed):
            return None

        # A new feature contract must reset all migration counters. For the same
        # contract, resume from the earliest pointer that is missing or carries a
        # different dataset version, including newly appended complete slates.
        if not _migration_is_current(state):
            _begin_migration(state, len(completed))
            state = handler._save_state(state)
        else:
            first_mismatch = _first_mismatched_pointer(completed)
            current_cursor = int(state.get("featureRematerializationCursor") or 0)
            cursor = min(max(0, current_cursor), first_mismatch, len(completed))
            state["featureRematerializationCursor"] = cursor
            state["featureRematerializedSlateCount"] = cursor
            state["featureRematerializationTotalSlateCount"] = len(completed)
            state["featureRematerializationComplete"] = False
            state["phase"] = "REMATERIALIZING_FEATURES"
            state["lastError"] = None
            state = handler._save_state(state)

        cursor = int(state.get("featureRematerializationCursor") or 0)
        if cursor < 0 or cursor > len(completed):
            _begin_migration(state, len(completed))
            cursor = 0
        stop = min(len(completed), cursor + BATCH_SIZE)
        for index in range(cursor, stop):
            day = str(completed[index].get("slateDateEt") or "")
            if not day:
                raise handler.OrchestrationError("completed slate missing date during rematerialization")
            try:
                completed[index] = _rebuild_slate(day)
            except Exception as exc:
                state["phase"] = "FEATURE_REMATERIALIZATION_BLOCKED"
                state["featureRematerializationComplete"] = False
                state["lastError"] = f"feature rematerialization failed for {day}: {type(exc).__name__}:{str(exc)[:300]}"
                errors = state.setdefault("featureRematerializationErrors", [])
                marker = {"slateDateEt": day, "error": state["lastError"], "recordedAtUtc": _now_iso()}
                if not errors or errors[-1].get("error") != marker["error"]:
                    errors.append(marker)
                state["completedSlates"] = completed
                handler._save_state(state)
                return {"ok": False, "status": state["phase"], "state": state}
            state["completedSlates"] = completed
            state["featureRematerializationCursor"] = index + 1
            state["featureRematerializedSlateCount"] = index + 1
            state["featureRematerializationTotalSlateCount"] = len(completed)
            state["featureRematerializationComplete"] = False
            state["lastRematerializedSlateDate"] = day
            state["featureRematerializationErrors"] = []
            handler._save_state(state)
        if stop < len(completed):
            latest = handler._load_state() or state
            return {"ok": True, "status": "REMATERIALIZING_FEATURES", "rematerializing": True, "state": latest}

        state = handler._load_state() or state
        completed = list(state.get("completedSlates") or completed)
        if _first_mismatched_pointer(completed) != len(completed):
            raise handler.OrchestrationError("rematerialization completed with mixed dataset pointers")
        state["eligibleGameCount"] = sum(int(row.get("eligibleGameCount") or 0) for row in completed)
        state["completeSlateCount"] = len(completed)
        state["featureDatasetVersion"] = FEATURE_DATASET_VERSION
        state["featureRematerializationVersion"] = VERSION
        state["featureRematerializationTargetDatasetVersion"] = FEATURE_DATASET_VERSION
        state["featureRematerializationTargetVersion"] = VERSION
        state["featureRematerializationComplete"] = True
        state["featureRematerializationCompletedAtUtc"] = _now_iso()
        state["featureRematerializationCursor"] = len(completed)
        state["featureRematerializedSlateCount"] = len(completed)
        state["featureRematerializationTotalSlateCount"] = len(completed)
        state["featureRematerializationPaidHistoricalCalls"] = 0
        state["featureRematerializationErrors"] = []
        if state.get("freshAuditExpansionRequired") is True:
            state["phase"] = "BACKFILLING"
        else:
            state["phase"] = "OPTIMIZING"
        state["lastError"] = None
        saved = handler._save_state(state)
        return {"ok": True, "status": "FEATURE_REMATERIALIZATION_COMPLETE", "rematerializing": False, "state": saved}
    finally:
        handler._release_lease(owner)
