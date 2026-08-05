"""Source-honest liveness classification for the MLB historical optimizer."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

VERSION = "MLB-HISTORICAL-LIVENESS-POLICY-v2-waiting-healthy"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"
ET = ZoneInfo("America/New_York")


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


def classify(
    state: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after_minutes: float = 75.0,
) -> Dict[str, Any]:
    """Separate stale source state from a healthy settled-horizon wait."""

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
    waiting_healthy = bool(
        phase == WAITING_PHASE
        and current_date is not None
        and current_date >= today_et
    )
    recovery_required = source_state_stale and not waiting_healthy
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
        "recoveryRequired": recovery_required,
        "heartbeatAtUtc": current_now.isoformat(),
    }
