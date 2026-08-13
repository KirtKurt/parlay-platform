from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Any


@dataclass(frozen=True)
class PullDecision:
    should_pull: bool
    reason: str
    next_interval_minutes: int
    next_due_at_utc: str
    urgency_score: float


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def desired_interval_minutes(hours_to_first_pitch: float | None,
                             volatility: float = 0.0,
                             missing_market_fraction: float = 0.0,
                             remaining_credits: int | None = None,
                             consecutive_empty_pulls: int = 0) -> int:
    """Adaptive cadence. A 5-minute heartbeat calls this function; API pulls happen only when due."""
    if hours_to_first_pitch is None:
        base = 60
    elif hours_to_first_pitch > 24:
        base = 60
    elif hours_to_first_pitch > 12:
        base = 30
    elif hours_to_first_pitch > 6:
        base = 15
    elif hours_to_first_pitch > 2:
        base = 10
    else:
        base = 5

    if volatility >= 0.025 or missing_market_fraction >= 0.35:
        base = min(base, 5)
    elif volatility >= 0.012 or missing_market_fraction >= 0.15:
        base = min(base, 10)

    if consecutive_empty_pulls >= 3:
        base = max(base, 30)
    if consecutive_empty_pulls >= 8:
        base = max(base, 60)

    if remaining_credits is not None:
        if remaining_credits < 50:
            base = max(base, 60)
        elif remaining_credits < 150:
            base = max(base, 30)
        elif remaining_credits < 400:
            base = max(base, 15)
    return int(base)


def earliest_future_start(events: Iterable[Mapping[str, Any]], now: datetime) -> datetime | None:
    starts = []
    for event in events:
        value = event.get('commence_time') or event.get('commenceTime')
        if not value:
            continue
        try:
            start = _dt(value)
        except Exception:
            continue
        if start > now:
            starts.append(start)
    return min(starts) if starts else None


def decide_pull(*, now: datetime, events: Iterable[Mapping[str, Any]], last_pull_at: str | datetime | None,
                volatility: float = 0.0, missing_market_fraction: float = 0.0,
                remaining_credits: int | None = None, consecutive_empty_pulls: int = 0,
                force_reason: str | None = None) -> PullDecision:
    now = _dt(now)
    first = earliest_future_start(events, now)
    h = ((first - now).total_seconds() / 3600.0) if first else None
    interval = desired_interval_minutes(h, volatility, missing_market_fraction, remaining_credits, consecutive_empty_pulls)

    if force_reason:
        return PullDecision(True, force_reason, interval, now.isoformat(), 1.0)
    if last_pull_at is None:
        return PullDecision(True, 'NO_PRIOR_PULL', interval, now.isoformat(), 1.0)

    last = _dt(last_pull_at)
    due = last.timestamp() + interval * 60
    due_dt = datetime.fromtimestamp(due, tz=timezone.utc)
    should = now >= due_dt
    urgency = max(0.0, min(1.0, (now.timestamp() - last.timestamp()) / max(1.0, interval * 60)))
    reason = 'ADAPTIVE_INTERVAL_DUE' if should else 'HEARTBEAT_ONLY_NOT_DUE'
    return PullDecision(should, reason, interval, due_dt.isoformat(), urgency)
