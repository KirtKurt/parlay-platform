"""Lease-independent MLB T-30/T-15 playability checkpoint scheduler.

The T-45 lock Lambda can legitimately spend many minutes validating a full
slate while its global execution lease suppresses overlapping lock runs.  This
entrypoint intentionally performs only the smaller, write-once assessment
sweep, so a long lock run cannot consume the complete T-15-to-start window.
It never creates or changes a winner, lock, outcome, daily card, or historical
record.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict


EVENT_RUN = "playability_checkpoint_sweep"


def _load_runtime() -> tuple[Any, Any]:
    """Install the protected runtime only when Lambda handles an event.

    Keeping this import lazy makes cold module inspection and unit-test
    collection side-effect-free.  Production still imports the protected
    runtime before resolving the narrow checkpoint runner, preserving its
    exact patch-install contract.
    """
    protected_runtime = importlib.import_module(
        "mlb_daily_pick_lock_protected"
    )
    daily_lock = importlib.import_module("mlb_daily_pick_lock")
    return protected_runtime, daily_lock


def _slate_date(event: Dict[str, Any]) -> str | None:
    for key in ("slateDateEt", "slate_date", "slateDate", "date"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    if (
        str(event.get("sport") or "").lower() != "mlb"
        or event.get("run") != EVENT_RUN
        or event.get("auto_ingest") is not False
    ):
        raise RuntimeError("MLB_PLAYABILITY_CHECKPOINT_EVENT_CONTRACT_INVALID")
    protected_runtime, daily_lock = _load_runtime()
    if protected_runtime.PER_GAME_LOCK_STATUS.get("ok") is not True:
        raise RuntimeError("MLB_PLAYABILITY_CHECKPOINT_RUNTIME_NOT_READY")
    runner = getattr(daily_lock, "run_playability_checkpoints", None)
    if not callable(runner):
        raise RuntimeError("MLB_PLAYABILITY_CHECKPOINT_RUNNER_NOT_INSTALLED")

    result = runner(_slate_date(event))
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"MLB_PLAYABILITY_CHECKPOINT_SWEEP_FAILED:{result}")
    return result
