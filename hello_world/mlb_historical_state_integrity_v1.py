"""State-integrity guards for the MLB historical optimizer.

The optimizer is scheduled frequently.  A settled-range cursor can legitimately be
unable to advance until yesterday's MLB slate is final, but that state is not active
backfilling and must not generate a new DynamoDB revision on every invocation.
"""
from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any, Mapping

import mlb_historical_incremental_range_extension_v1 as incremental_range_extension

VERSION = "MLB-HISTORICAL-STATE-INTEGRITY-v1-settled-horizon-idempotent"
WAITING_PHASE = "WAITING_FOR_SETTLED_HORIZON"
_VOLATILE_STATE_FIELDS = frozenset({"revision", "updatedAtUtc"})


def _date(value: Any, fallback: str) -> date:
    try:
        return date.fromisoformat(str(value or fallback))
    except ValueError:
        return date.fromisoformat(fallback)


def _material(handler: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    value = handler._migrate_state(copy.deepcopy(dict(state or {})))
    value["version"] = handler.VERSION
    for key in _VOLATILE_STATE_FIELDS:
        value.pop(key, None)
    return value


def install(handler: Any, base: Any) -> None:
    """Install idempotent state writes and an honest settled-horizon phase."""
    if getattr(handler, "_INQSI_HISTORICAL_STATE_INTEGRITY_V1_INSTALLED", False):
        return

    original_save_state = handler._save_state

    def save_state_if_changed(state: Mapping[str, Any]) -> dict[str, Any]:
        candidate = handler._migrate_state(copy.deepcopy(dict(state or {})))
        candidate["version"] = handler.VERSION
        current = handler._load_state()
        if isinstance(current, Mapping) and _material(handler, current) == _material(handler, candidate):
            return copy.deepcopy(dict(current))
        return original_save_state(candidate)

    handler._save_state = save_state_if_changed

    original_append = base._append_authorized_range_extension

    def append_with_settled_horizon_state() -> None:
        state = handler._load_state()
        if isinstance(state, dict):
            previous_end = _date(state.get("endDate"), handler.END_DATE)
            configured_end = _date(handler.END_DATE, handler.END_DATE)
            horizon = min(configured_end, incremental_range_extension.settled_horizon())
            if state.get("phase") == WAITING_PHASE and horizon > previous_end:
                resumed = copy.deepcopy(state)
                resumed["phase"] = "DATA_RANGE_EXHAUSTED"
                resumed["lastError"] = None
                resumed.pop("settledHorizonWait", None)
                handler._save_state(resumed)

        original_append()

        state = handler._load_state()
        if not isinstance(state, dict):
            return
        previous_end = _date(state.get("endDate"), handler.END_DATE)
        configured_end = _date(handler.END_DATE, handler.END_DATE)
        horizon = min(configured_end, incremental_range_extension.settled_horizon())
        current = _date(state.get("currentDate"), previous_end.isoformat())
        should_wait = (
            configured_end > previous_end
            and horizon <= previous_end
            and current > previous_end
            and state.get("phase") in {
                "BACKFILLING",
                "DATA_RANGE_EXHAUSTED",
                WAITING_PHASE,
            }
        )
        if not should_wait:
            return

        waiting = copy.deepcopy(state)
        waiting["phase"] = WAITING_PHASE
        waiting["lastError"] = None
        waiting["rangeExtensionNextRetryDate"] = (previous_end + timedelta(days=1)).isoformat()
        waiting["settledHorizonWait"] = {
            "version": VERSION,
            "authorizedThroughDate": previous_end.isoformat(),
            "settledHorizonDate": horizon.isoformat(),
            "configuredCeilingDate": configured_end.isoformat(),
            "nextEligibleSlateDate": (previous_end + timedelta(days=1)).isoformat(),
            "blockingError": False,
        }
        handler._save_state(waiting)

    base._append_authorized_range_extension = append_with_settled_horizon_state
    handler._INQSI_HISTORICAL_STATE_INTEGRITY_V1_INSTALLED = True
