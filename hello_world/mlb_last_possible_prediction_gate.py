from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

try:
    import inqsi_pull_history as history
except Exception:
    history = None

try:
    import mlb_fundamentals_snapshot_v2 as fundamentals_v2
except Exception:
    fundamentals_v2 = None

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
FINAL_GATE_START_MINUTES = int(os.environ.get("INQSI_MLB_FINAL_GATE_START_MINUTES", "720"))
FINAL_GATE_END_MINUTES = int(os.environ.get("INQSI_MLB_FINAL_GATE_END_MINUTES", "10"))
POLICY_VERSION = (
    "MLB-LAST-POSSIBLE-PREDICTION-GATE-v5-12H-INDIVIDUAL-GAME-"
    "FUNDAMENTALS-V2-SEPARATE-FROM-TIME-LOCK"
)
SLATE_LOCK_POLICY_VERSION = "MLB-SLATE-WIDE-PREDICTION-LOCK-v1-45MIN"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _minutes_to_start(row: Dict[str, Any]) -> float | None:
    dt = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
    if not dt:
        return None
    return round((dt - _now_utc()).total_seconds() / 60.0, 2)


def _phase(minutes_to_start: float | None) -> str:
    if minutes_to_start is None:
        return "UNKNOWN_START_TIME"
    if minutes_to_start < 0:
        return "GAME_STARTED_OR_CLOSED"
    if minutes_to_start < FINAL_GATE_END_MINUTES:
        return "LOCK_CLOSED"
    if minutes_to_start <= FINAL_GATE_START_MINUTES:
        return "FINAL_GATE_OPEN"
    return "PRE_FINAL_GATE"


def _fundamentals_v2_status(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return source-honest V2 completeness without crediting shadow context.

    Only the canonical, validated V2 snapshot can satisfy this gate. Generic
    optimizer flags and provider shadow payloads are deliberately ignored.
    """
    snapshot = row.get("fundamentalsSnapshotV2")
    reasons = []
    if not isinstance(snapshot, dict):
        reasons.append("fundamentals_v2_snapshot_missing")
    elif fundamentals_v2 is None:
        reasons.append("fundamentals_v2_validator_unavailable")
    else:
        reasons.extend(str(reason) for reason in fundamentals_v2.validate(snapshot))
        if snapshot.get("pregameComplete") is not True:
            reasons.append("fundamentals_v2_pregame_incomplete")
        if snapshot.get("trainingEligibleAtCapture") is not True:
            reasons.append("fundamentals_v2_capture_not_eligible")
    reasons = sorted(set(reason for reason in reasons if reason))
    return {
        "applied": not reasons,
        "snapshotVersion": snapshot.get("version") if isinstance(snapshot, dict) else None,
        "snapshotFingerprint": (
            snapshot.get("fingerprint") if isinstance(snapshot, dict) else None
        ),
        "completenessRatio": (
            snapshot.get("completenessRatio") if isinstance(snapshot, dict) else 0.0
        ),
        "missingGroups": (
            sorted(snapshot.get("missingGroups") or [])
            if isinstance(snapshot, dict)
            else []
        ),
        "validationReasons": reasons,
    }


def _final_data_status(final_locked: bool, fundamentals_applied: bool) -> str:
    if final_locked and fundamentals_applied:
        return "TIME_LOCKED_FUNDAMENTALS_V2_COMPLETE"
    if final_locked:
        return "TIME_LOCKED_FUNDAMENTALS_V2_INCOMPLETE"
    if fundamentals_applied:
        return "PRE_LOCK_FUNDAMENTALS_V2_COMPLETE"
    return "PRE_LOCK_FUNDAMENTALS_V2_INCOMPLETE"


def _prediction_source(fundamentals_applied: bool, scope: str) -> str:
    completeness = (
        "WITH_VALIDATED_FUNDAMENTALS_V2"
        if fundamentals_applied
        else "WITHOUT_COMPLETE_FUNDAMENTALS_V2"
    )
    return f"CANONICAL_MARKET_AND_LEARNING_{completeness}_{scope}"


def _store_final(row: Dict[str, Any], module: Any = None) -> Dict[str, Any]:
    """Delegate final rows through the canonical prediction store.

    Direct DynamoDB writes here previously bypassed the immutable exact-vector
    guard. The outer finalizer normally suppresses this early store; direct
    callers are still protected by the module's central `_store_prediction`.
    """
    if module is None or not hasattr(module, "_store_prediction"):
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    try:
        stored = module._store_prediction(row)
        return {**(stored or {}), "finalGateStored": bool((stored or {}).get("ok"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _annotate_from_slate_lock(out: Dict[str, Any], persist: bool = False, module: Any = None) -> Dict[str, Any]:
    lock = dict(out.get("slatePredictionLock") or {})
    fundamentals = _fundamentals_v2_status(out)
    fundamentals_applied = bool(fundamentals["applied"])
    final_locked = bool(lock.get("locked"))
    full_data_final_pick = bool(final_locked and fundamentals_applied)
    phase = "SLATE_LOCKED" if final_locked else "PRE_SLATE_LOCK"
    policy_version = lock.get("policyVersion") or SLATE_LOCK_POLICY_VERSION

    out["lastPossiblePredictionGate"] = {
        "policyVersion": policy_version,
        "phase": phase,
        "minutesToStart": lock.get("minutesUntilFirstGameStart"),
        "gateWindowMinutesBeforeStart": {
            "opensAt": lock.get("lockMinutesBeforeFirstGame", 45),
            "closesAt": lock.get("lockMinutesBeforeFirstGame", 45),
            "meaning": "single_slate_wide_lock_cutoff",
        },
        "slateWideLock": True,
        "lockMinutesBeforeFirstGame": lock.get("lockMinutesBeforeFirstGame", 45),
        "firstGameStartUtc": lock.get("firstGameStartUtc"),
        "lockAtUtc": lock.get("lockAtUtc"),
        "latestAvailablePullAt": lock.get("latestAvailablePullAt"),
        "latestScoringPullAt": lock.get("latestScoringPullAt"),
        "totalPullCountAvailable": lock.get("totalPullCountAvailable"),
        "scoringPullCount": lock.get("scoringPullCount"),
        "finalWindowActive": final_locked,
        "finalLocked": final_locked,
        "timeLockFinal": final_locked,
        "fundamentalsV2Applied": fundamentals_applied,
        "fundamentalsV2Complete": fundamentals_applied,
        "fundamentalsV2SnapshotVersion": fundamentals["snapshotVersion"],
        "fundamentalsV2SnapshotFingerprint": fundamentals["snapshotFingerprint"],
        "fundamentalsV2CompletenessRatio": fundamentals["completenessRatio"],
        "fundamentalsV2MissingGroups": fundamentals["missingGroups"],
        "fundamentalsV2ValidationReasons": fundamentals["validationReasons"],
        "fundamentalsV2RequiredForFullDataFinalPick": True,
        "fullDataFinalPick": full_data_final_pick,
        "finalDataStatus": _final_data_status(
            final_locked, fundamentals_applied
        ),
        "predictionSource": _prediction_source(
            fundamentals_applied, "SLATE_LOCK"
        ),
        "rules": [
            "All MLB predictions for the slate lock together 45 minutes before the first game begins.",
            "Once the slate is locked, later 15-minute pulls do not change any game-winner prediction on that slate.",
            "Locked predictions are scored from pull history captured at or before the slate lock timestamp.",
            "Time-lock finality is independent from Fundamentals V2 completeness.",
            "A full-data final pick requires both a final time lock and a validated complete Fundamentals V2 snapshot.",
        ],
    }
    tags = sorted(set(out.get("tags") or []))
    tags.append("SLATE_WIDE_45_MIN_LOCK_POLICY")
    if final_locked:
        tags.extend(["SLATE_LOCKED", "FINAL_LOCKED"])
    else:
        tags.append("PRE_SLATE_LOCK")
    if fundamentals_applied:
        tags.append("FUNDAMENTALS_V2_COMPLETE")
    else:
        tags.append("FUNDAMENTALS_V2_INCOMPLETE")
        if final_locked:
            tags.append("FINAL_LOCKED_WITHOUT_COMPLETE_FUNDAMENTALS_V2")
    out["fullDataFinalPick"] = full_data_final_pick
    out["tags"] = sorted(set(tags))
    if persist:
        out["finalGateStored"] = _store_final(out, module=module)
    return out


def annotate_prediction(row: Dict[str, Any], persist: bool = False, module: Any = None) -> Dict[str, Any]:
    out = dict(row or {})
    if (out.get("slatePredictionLock") or {}).get("slateWideLock"):
        return _annotate_from_slate_lock(out, persist=persist, module=module)

    minutes = _minutes_to_start(out)
    phase = _phase(minutes)
    fundamentals = _fundamentals_v2_status(out)
    fundamentals_applied = bool(fundamentals["applied"])
    final_window = phase == "FINAL_GATE_OPEN"
    closed = phase in {"LOCK_CLOSED", "GAME_STARTED_OR_CLOSED"}
    final_locked = final_window or closed
    full_data_final_pick = bool(final_locked and fundamentals_applied)

    out["lastPossiblePredictionGate"] = {
        "policyVersion": POLICY_VERSION,
        "phase": phase,
        "minutesToStart": minutes,
        "gateWindowMinutesBeforeStart": {"opensAt": FINAL_GATE_START_MINUTES, "closesAt": FINAL_GATE_END_MINUTES},
        "finalWindowActive": final_window,
        "finalLocked": final_locked,
        "timeLockFinal": final_locked,
        "fundamentalsV2Applied": fundamentals_applied,
        "fundamentalsV2Complete": fundamentals_applied,
        "fundamentalsV2SnapshotVersion": fundamentals["snapshotVersion"],
        "fundamentalsV2SnapshotFingerprint": fundamentals["snapshotFingerprint"],
        "fundamentalsV2CompletenessRatio": fundamentals["completenessRatio"],
        "fundamentalsV2MissingGroups": fundamentals["missingGroups"],
        "fundamentalsV2ValidationReasons": fundamentals["validationReasons"],
        "fundamentalsV2RequiredForFullDataFinalPick": True,
        "fullDataFinalPick": full_data_final_pick,
        "finalDataStatus": _final_data_status(
            final_locked, fundamentals_applied
        ),
        "predictionSource": _prediction_source(
            fundamentals_applied, "INDIVIDUAL_GAME_GATE"
        ),
        "rules": [
            f"Open the final gate {FINAL_GATE_START_MINUTES} minutes before each individual game.",
            "This fallback is only used when the slate-wide lock wrapper is absent.",
            "Time-lock finality is independent from Fundamentals V2 completeness.",
            "A full-data final pick requires both a final time lock and a validated complete Fundamentals V2 snapshot.",
        ],
    }
    tags = sorted(set(out.get("tags") or []))
    if final_window:
        tags.append("FINAL_GATE_OPEN")
    if final_locked:
        tags.append("FINAL_LOCKED")
    if fundamentals_applied:
        tags.append("FUNDAMENTALS_V2_COMPLETE")
    else:
        tags.append("FUNDAMENTALS_V2_INCOMPLETE")
        if final_locked:
            tags.append("FINAL_LOCKED_WITHOUT_COMPLETE_FUNDAMENTALS_V2")
    out["fullDataFinalPick"] = full_data_final_pick
    out["tags"] = sorted(set(tags))
    if persist:
        out["finalGateStored"] = _store_final(out, module=module)
    return out


def annotate_result(result: Dict[str, Any], persist: bool = False, module: Any = None) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    predictions = [annotate_prediction(row, persist=persist, module=module) for row in (result.get("predictions") or [])]
    predictions.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
    phases: Dict[str, int] = {}
    for row in predictions:
        phase = (row.get("lastPossiblePredictionGate") or {}).get("phase") or "UNKNOWN"
        phases[phase] = phases.get(phase, 0) + 1
    fundamentals_applied = [
        row
        for row in predictions
        if (row.get("lastPossiblePredictionGate") or {}).get(
            "fundamentalsV2Applied"
        )
        is True
    ]
    final_rows = [row for row in predictions if (row.get("lastPossiblePredictionGate") or {}).get("finalLocked")]
    incomplete_final_rows = [
        row
        for row in final_rows
        if (row.get("lastPossiblePredictionGate") or {}).get(
            "fundamentalsV2Applied"
        )
        is not True
    ]
    full_data_rows = [row for row in predictions if row.get("fullDataFinalPick")]
    summary = dict(result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {})
    lock = result.get("slatePredictionLock") or ((predictions[0].get("slatePredictionLock") if predictions else {}) or {})
    if lock.get("slateWideLock"):
        policy_version = lock.get("policyVersion") or SLATE_LOCK_POLICY_VERSION
        gate_window = {"opensAt": lock.get("lockMinutesBeforeFirstGame", 45), "closesAt": lock.get("lockMinutesBeforeFirstGame", 45), "meaning": "single_slate_wide_lock_cutoff"}
    else:
        policy_version = POLICY_VERSION
        gate_window = {"opensAt": FINAL_GATE_START_MINUTES, "closesAt": FINAL_GATE_END_MINUTES}
    summary["lastPossiblePredictionGate"] = {
        "applied": True,
        "policyVersion": policy_version,
        "slateWideLock": bool(lock.get("slateWideLock")),
        "lockAtUtc": lock.get("lockAtUtc"),
        "firstGameStartUtc": lock.get("firstGameStartUtc"),
        "latestScoringPullAt": lock.get("latestScoringPullAt"),
        "gateWindowMinutesBeforeStart": gate_window,
        "phaseCounts": phases,
        "finalLockedCount": len(final_rows),
        "timeLockFinalCount": len(final_rows),
        "fundamentalsV2AppliedCount": len(fundamentals_applied),
        "fundamentalsV2IncompleteAtFinalLockCount": len(incomplete_final_rows),
        "fundamentalsV2RequiredForFullDataFinalPick": True,
        "timeLockFinalityIndependentFromFundamentalsV2": True,
        "shadowEvidenceCreditedForFundamentalsV2": False,
        "fullDataFinalPickCount": len(full_data_rows),
        "fullDataFinalPickInvariant": (
            "finalLocked AND validated complete Fundamentals V2 snapshot"
        ),
        "persistedFinalRows": bool(persist),
    }
    out = dict(result)
    out["predictions"] = predictions
    out["count"] = len(predictions)
    out["lastPossiblePredictionGate"] = summary["lastPossiblePredictionGate"]
    out["rolling24hAccuracyTarget"] = summary
    out["accuracyTarget"] = summary
    suffix = (
        "+slate-wide-45min-final-gate"
        if lock.get("slateWideLock")
        else "+last-possible-gate-v5-12h-individual-game-fundamentals-v2"
    )
    if suffix not in str(result.get("modelVersion") or ""):
        out["modelVersion"] = str(result.get("modelVersion") or "") + suffix
    return out


def apply(module):
    if getattr(module, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def patched_predict_all(*args, **kwargs):
        persist = bool(kwargs.get("store"))
        result = original_predict_all(*args, **kwargs)
        if getattr(module, "_INQSI_MLB_CANONICAL_PER_GAME_AUTHORITY_ENABLED", False):
            # The per-game T-45 authority owns finality.  This legacy T-12h
            # display gate must not mark live candidates locked or prevent
            # their immutable pre-lock snapshots from being persisted.
            return result
        return annotate_result(result, persist=persist, module=module)

    module.predict_all = patched_predict_all
    module._INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED = True
    return module
