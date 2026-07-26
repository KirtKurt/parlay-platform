"""Resumable V8-supervised feature rematerialization from immutable archives.

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
from typing import Any, Dict, Mapping, Optional

import mlb_historical_optimizer_handler as handler

VERSION = "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v2.0-v8-supervised-shadow"
FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v8-supervised-shadow-ready"
BATCH_SIZE = max(1, min(10, int(os.environ.get("MLB_HISTORICAL_REMATERIALIZE_SLATES_PER_RUN", "5"))))
ELIGIBLE_PHASES = {
    "DATA_RANGE_EXHAUSTED",
    "CANDIDATE_REJECTED",
    "OPTIMIZING",
    "BACKFILLING",
    "PAUSED_QUOTA",
    "REMATERIALIZING_FEATURES",
    "FEATURE_REMATERIALIZATION_BLOCKED",
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
    records = dataset.get("records") or []
    v8_feature_count = sum(
        isinstance((row.get("homeSignal") or {}).get("oddsMarketExpansionFeatures"), Mapping)
        and bool((row.get("homeSignal") or {}).get("oddsMarketExpansionFeatures"))
        for row in records
        if isinstance(row, Mapping)
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
        "v8ShadowFeatureRecordCount": int(v8_feature_count),
    }


def run_once() -> Optional[Dict[str, Any]]:
    owner = f"feature-rematerialization-{uuid.uuid4()}"
    if not handler._acquire_lease(owner):
        return {"ok": True, "status": "LEASE_HELD", "rematerializing": True}
    try:
        state = handler._load_state()
        if not isinstance(state, dict):
            return None
        if state.get("featureDatasetVersion") == FEATURE_DATASET_VERSION and state.get("featureRematerializationComplete") is True:
            return None
        if state.get("phase") not in ELIGIBLE_PHASES:
            return None
        completed = sorted(
            [copy.deepcopy(row) for row in state.get("completedSlates") or [] if isinstance(row, Mapping)],
            key=lambda row: str(row.get("slateDateEt") or ""),
        )
        if not completed:
            return None
        prior_phase = state.get("phase")
        if prior_phase not in {"REMATERIALIZING_FEATURES", "FEATURE_REMATERIALIZATION_BLOCKED"}:
            state["featureRematerializationPreviousPhase"] = prior_phase
            state["featureRematerializationStartedAtUtc"] = _now_iso()
            state["featureRematerializationCursor"] = 0
            state["featureRematerializationErrors"] = []
        state["phase"] = "REMATERIALIZING_FEATURES"
        state["lastError"] = None
        cursor = int(state.get("featureRematerializationCursor") or 0)
        stop = min(len(completed), cursor + BATCH_SIZE)
        for index in range(cursor, stop):
            day = str(completed[index].get("slateDateEt") or "")
            if not day:
                raise handler.OrchestrationError("completed slate missing date during rematerialization")
            try:
                completed[index] = _rebuild_slate(day)
            except Exception as exc:
                state["phase"] = "FEATURE_REMATERIALIZATION_BLOCKED"
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
            state["lastRematerializedSlateDate"] = day
            state["featureRematerializationErrors"] = []
            handler._save_state(state)
        if stop < len(completed):
            latest = handler._load_state() or state
            return {"ok": True, "status": "REMATERIALIZING_FEATURES", "rematerializing": True, "state": latest}

        state = handler._load_state() or state
        completed = list(state.get("completedSlates") or completed)
        state["eligibleGameCount"] = sum(int(row.get("eligibleGameCount") or 0) for row in completed)
        state["completeSlateCount"] = len(completed)
        state["featureDatasetVersion"] = FEATURE_DATASET_VERSION
        state["featureRematerializationVersion"] = VERSION
        state["featureRematerializationComplete"] = True
        state["featureRematerializationCompletedAtUtc"] = _now_iso()
        state["featureRematerializedSlateCount"] = len(completed)
        state["featureRematerializationPaidHistoricalCalls"] = 0
        state["featureRematerializationErrors"] = []
        state["v8ShadowFeatureRecordCount"] = sum(
            int(row.get("v8ShadowFeatureRecordCount") or 0) for row in completed
        )
        if state.get("freshAuditExpansionRequired") is True:
            state["phase"] = "BACKFILLING"
        else:
            state["phase"] = "OPTIMIZING"
        state["lastError"] = None
        saved = handler._save_state(state)
        return {"ok": True, "status": "FEATURE_REMATERIALIZATION_COMPLETE", "rematerializing": False, "state": saved}
    finally:
        handler._release_lease(owner)
