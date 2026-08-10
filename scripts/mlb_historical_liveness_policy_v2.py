"""Source-honest liveness classification for the MLB historical optimizer."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

VERSION = "MLB-HISTORICAL-LIVENESS-POLICY-v3-settled-evidence"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"
ET = ZoneInfo("America/New_York")
_OFFICIAL_OFF_DAY_REASON = "official_off_day"


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _latest_completed_slate_date(state: Mapping[str, Any]) -> Optional[date]:
    values = [
        parsed
        for row in state.get("completedSlates") or []
        if isinstance(row, Mapping)
        for parsed in [_parse_date(row.get("slateDateEt"))]
        if parsed is not None
    ]
    return max(values) if values else None


def _planned_slate_dates(state: Mapping[str, Any]) -> set[date]:
    plan = state.get("plan") or {}
    if not isinstance(plan, Mapping):
        return set()
    return {
        parsed
        for row in plan.get("slates") or []
        if isinstance(row, Mapping)
        for parsed in [_parse_date(row.get("slateDateEt"))]
        if parsed is not None
    }


def _rejection_reasons_for_date(
    state: Mapping[str, Any], target: date
) -> set[str]:
    reasons: set[str] = set()
    for row in state.get("rejectedSlates") or []:
        if not isinstance(row, Mapping):
            continue
        if _parse_date(row.get("slateDateEt")) != target:
            continue
        reason = str(row.get("reason") or "").strip()
        if reason:
            reasons.add(reason)
    return reasons


def _waiting_evidence(
    state: Mapping[str, Any], *, today_et: date
) -> Dict[str, Any]:
    """Validate that a temporal wait has complete evidence through yesterday."""

    horizon = today_et - timedelta(days=1)
    current_date = _parse_date(state.get("currentDate"))
    proof = state.get("settledHorizonWait")
    proof_map = proof if isinstance(proof, Mapping) else {}
    authorized_through = _parse_date(proof_map.get("authorizedThroughDate"))
    proof_horizon = _parse_date(proof_map.get("settledHorizonDate"))
    next_eligible = _parse_date(proof_map.get("nextEligibleSlateDate"))
    configured_ceiling = _parse_date(proof_map.get("configuredCeilingDate"))
    retry_date = _parse_date(state.get("rangeExtensionNextRetryDate"))
    latest_completed = _latest_completed_slate_date(state)
    horizon_rejections = _rejection_reasons_for_date(state, horizon)
    plan = state.get("plan") or {}
    plan_map = plan if isinstance(plan, Mapping) else {}
    plan_end = _parse_date(
        plan_map.get("endDate")
        or plan_map.get("plannedThroughDate")
        or state.get("endDate")
    )
    planned_dates = _planned_slate_dates(state)
    ledger_proves_off_day = bool(
        plan_map
        and plan_map.get("completeDateRangeLedger") is True
        and int(plan_map.get("planningErrorCount") or 0) == 0
        and not plan_map.get("rejectedDates")
        and plan_end is not None
        and plan_end >= horizon
        and horizon not in planned_dates
    )

    errors: list[str] = []
    if current_date != today_et:
        errors.append("optimizer_cursor_not_at_current_et_day")
    if not isinstance(proof, Mapping):
        errors.append("settled_horizon_wait_proof_missing")
    if authorized_through != horizon:
        errors.append("authorized_through_not_at_settled_horizon")
    if proof_horizon != horizon:
        errors.append("settled_horizon_date_mismatch")
    if next_eligible != today_et:
        errors.append("next_eligible_slate_date_mismatch")
    if retry_date != today_et:
        errors.append("range_extension_retry_date_mismatch")
    if configured_ceiling is None or configured_ceiling < today_et:
        errors.append("configured_ceiling_does_not_cover_current_day")
    if proof_map.get("blockingError") is not False:
        errors.append("settled_horizon_wait_has_blocking_error")
    if state.get("lastError") not in (None, ""):
        errors.append("optimizer_last_error_present")

    horizon_evidence: Optional[str] = None
    if latest_completed == horizon:
        horizon_evidence = "IMMUTABLE_COMPLETE_SLATE"
    elif horizon_rejections == {_OFFICIAL_OFF_DAY_REASON}:
        horizon_evidence = "OFFICIAL_OFF_DAY_REJECTION"
    elif ledger_proves_off_day:
        horizon_evidence = "OFFICIAL_OFF_DAY_LEDGER"
    else:
        errors.append("settled_horizon_completion_evidence_missing")

    return {
        "valid": not errors,
        "errors": errors,
        "settledHorizonDateEt": horizon.isoformat(),
        "authorizedThroughDateEt": (
            authorized_through.isoformat() if authorized_through else None
        ),
        "nextEligibleSlateDateEt": (
            next_eligible.isoformat() if next_eligible else None
        ),
        "latestCompletedSlateDateEt": (
            latest_completed.isoformat() if latest_completed else None
        ),
        "settledHorizonEvidence": horizon_evidence,
        "settledHorizonRejectionReasons": sorted(horizon_rejections),
    }


def classify(
    state: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after_minutes: float = 75.0,
) -> Dict[str, Any]:
    """Separate a proven temporal wait from a masked ingestion stall."""

    current_now = now or datetime.now(timezone.utc)
    if current_now.tzinfo is None:
        current_now = current_now.replace(tzinfo=timezone.utc)
    current_now = current_now.astimezone(timezone.utc)
    phase = str(state.get("phase") or "")
    current_date = _parse_date(state.get("currentDate"))
    today_et = current_now.astimezone(ET).date()
    updated = _parse_utc(
        state.get("updatedAtUtc") or state.get("stateUpdatedAtUtc")
    )
    age_minutes = (
        (current_now - updated).total_seconds() / 60.0 if updated is not None else None
    )
    source_state_stale = bool(
        updated is None or age_minutes is None or age_minutes > stale_after_minutes
    )

    latest_completed = _latest_completed_slate_date(state)
    waiting_evidence = (
        _waiting_evidence(state, today_et=today_et)
        if phase == WAITING_PHASE
        else {
            "valid": False,
            "errors": [],
            "settledHorizonDateEt": (today_et - timedelta(days=1)).isoformat(),
            "authorizedThroughDateEt": None,
            "nextEligibleSlateDateEt": None,
            "latestCompletedSlateDateEt": (
                latest_completed.isoformat() if latest_completed else None
            ),
            "settledHorizonEvidence": None,
            "settledHorizonRejectionReasons": [],
        }
    )
    waiting_healthy = bool(phase == WAITING_PHASE and waiting_evidence["valid"])
    invalid_wait = bool(phase == WAITING_PHASE and not waiting_healthy)
    recovery_required = bool(invalid_wait or (source_state_stale and not waiting_healthy))

    if waiting_healthy:
        status = "WAITING_HEALTHY"
    elif recovery_required:
        status = "RECOVERY_REQUIRED"
    else:
        status = "SOURCE_STATE_FRESH"
    return {
        "version": VERSION,
        "status": status,
        "phase": phase or None,
        "optimizerCurrentDate": current_date.isoformat() if current_date else None,
        "todayEt": today_et.isoformat(),
        "sourceStateUpdatedAtUtc": updated.isoformat() if updated else None,
        "sourceStateAgeMinutes": age_minutes,
        "sourceStateStale": source_state_stale,
        "waitingHealthy": waiting_healthy,
        "waitingProofValid": waiting_evidence["valid"],
        "waitingProofErrors": waiting_evidence["errors"],
        "settledHorizonDateEt": waiting_evidence["settledHorizonDateEt"],
        "authorizedThroughDateEt": waiting_evidence["authorizedThroughDateEt"],
        "nextEligibleSlateDateEt": waiting_evidence["nextEligibleSlateDateEt"],
        "latestCompletedSlateDateEt": waiting_evidence[
            "latestCompletedSlateDateEt"
        ],
        "settledHorizonEvidence": waiting_evidence["settledHorizonEvidence"],
        "settledHorizonRejectionReasons": waiting_evidence[
            "settledHorizonRejectionReasons"
        ],
        "recoveryRequired": recovery_required,
        "heartbeatAtUtc": current_now.isoformat(),
    }
