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
    information_gain_score: float


def _dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        d = value
    else:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def information_gain_score(*, hours_to_first_pitch: float | None,
                           volatility: float = 0.0,
                           missing_market_fraction: float = 0.0,
                           recent_signal_change: float = 0.0,
                           new_event_fraction: float = 0.0) -> float:
    """Estimate how valuable another live pull is. No quota/cost terms are allowed here."""
    proximity = 0.15 if hours_to_first_pitch is None else max(0.0, min(1.0, 1.0 - hours_to_first_pitch / 24.0))
    score = (
        0.38 * proximity
        + 0.24 * min(1.0, abs(volatility) / 0.03)
        + 0.18 * min(1.0, max(0.0, missing_market_fraction))
        + 0.12 * min(1.0, abs(recent_signal_change) / 0.03)
        + 0.08 * min(1.0, max(0.0, new_event_fraction))
    )
    return max(0.0, min(1.0, score))


def desired_interval_minutes(hours_to_first_pitch: float | None,
                             volatility: float = 0.0,
                             missing_market_fraction: float = 0.0,
                             recent_signal_change: float = 0.0,
                             new_event_fraction: float = 0.0) -> int:
    """Autonomous cadence driven only by information value, never API cost."""
    gain = information_gain_score(
        hours_to_first_pitch=hours_to_first_pitch,
        volatility=volatility,
        missing_market_fraction=missing_market_fraction,
        recent_signal_change=recent_signal_change,
        new_event_fraction=new_event_fraction,
    )
    if gain >= 0.72:
        return 5
    if gain >= 0.52:
        return 10
    if gain >= 0.32:
        return 15
    if gain >= 0.18:
        return 30
    return 60


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
                recent_signal_change: float = 0.0, new_event_fraction: float = 0.0,
                force_reason: str | None = None) -> PullDecision:
    now = _dt(now)
    first = earliest_future_start(events, now)
    h = ((first - now).total_seconds() / 3600.0) if first else None
    gain = information_gain_score(
        hours_to_first_pitch=h,
        volatility=volatility,
        missing_market_fraction=missing_market_fraction,
        recent_signal_change=recent_signal_change,
        new_event_fraction=new_event_fraction,
    )
    interval = desired_interval_minutes(h, volatility, missing_market_fraction, recent_signal_change, new_event_fraction)

    if force_reason:
        return PullDecision(True, force_reason, interval, now.isoformat(), 1.0, gain)
    if last_pull_at is None:
        return PullDecision(True, 'NO_PRIOR_PULL', interval, now.isoformat(), 1.0, gain)

    last = _dt(last_pull_at)
    due = last.timestamp() + interval * 60
    due_dt = datetime.fromtimestamp(due, tz=timezone.utc)
    should = now >= due_dt
    urgency = max(0.0, min(1.0, (now.timestamp() - last.timestamp()) / max(1.0, interval * 60)))
    reason = 'INFORMATION_GAIN_PULL_DUE' if should else 'HEARTBEAT_ONLY_NOT_DUE'
    return PullDecision(should, reason, interval, due_dt.isoformat(), urgency, gain)
