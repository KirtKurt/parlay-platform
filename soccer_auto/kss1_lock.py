"""KSS1 lock policy.

Training snapshot remains T-45. Public engine authority is T-60.
Existing soccer_auto production bind is still T-10 until a dedicated
cutover PR updates inference.py + lock-contract tests together.
This module is the contract the goals engine must obey.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

TRAINING_HORIZON = "T45"
PUBLIC_HORIZON = "T60"
LEGACY_PUBLIC_HORIZON = "T10"
TRAINING_MINUTES = 45
PUBLIC_MINUTES = 60
ENGINE_LOCK_VERSION = "kss1-t60-lock-v1"


def parse_utc(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        raise ValueError("kickoff must be timezone-aware")
    return stamp.astimezone(timezone.utc)


def lock_instant(commence_time: str, minutes: int) -> datetime:
    return parse_utc(commence_time) - timedelta(minutes=minutes)


def public_lock_at(commence_time: str) -> datetime:
    return lock_instant(commence_time, PUBLIC_MINUTES)


def training_lock_at(commence_time: str) -> datetime:
    return lock_instant(commence_time, TRAINING_MINUTES)


def classify_observation(commence_time: str, observed_at: str) -> dict[str, Any]:
    kickoff = parse_utc(commence_time)
    observed = parse_utc(observed_at)
    public_deadline = kickoff - timedelta(minutes=PUBLIC_MINUTES)
    training_deadline = kickoff - timedelta(minutes=TRAINING_MINUTES)
    if observed > kickoff:
        return {"action": "reject", "reason": "AFTER_KICKOFF", "horizon": None}
    if observed <= public_deadline:
        return {
            "action": "public_eligible",
            "reason": "ON_OR_BEFORE_T60",
            "horizon": PUBLIC_HORIZON,
            "lock_at": public_deadline.isoformat().replace("+00:00", "Z"),
        }
    if observed <= training_deadline:
        return {
            "action": "training_only",
            "reason": "MISSED_T60_WITHIN_T45",
            "horizon": TRAINING_HORIZON,
            "lock_at": None,
        }
    return {
        "action": "no_public_pick",
        "reason": "MISSED_T60",
        "horizon": None,
        "lock_at": None,
    }


def void_for_postponement(existing_lock: dict[str, Any], new_commence_time: str) -> dict[str, Any]:
    old = str(existing_lock.get("commence_time") or "")
    if old == new_commence_time:
        return {"void": False, "reason": "KICKOFF_UNCHANGED"}
    return {
        "void": True,
        "reason": "KICKOFF_REVISED",
        "prior_lock_version": existing_lock.get("lock_version"),
        "prior_commence_time": old,
        "new_commence_time": new_commence_time,
        "reschedule_public_lock_at": public_lock_at(new_commence_time).isoformat().replace("+00:00", "Z"),
        "preserve_audit": True,
        "relock_on_lineup": False,
    }


def first_bind_wins(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if existing and existing.get("horizon") == PUBLIC_HORIZON and existing.get("immutable"):
        return {"accepted": False, "reason": "FIRST_T60_BIND_IMMUTABLE", "authority": existing}
    return {"accepted": True, "reason": "FIRST_VALID_T60_BIND", "authority": candidate}
