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
    d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def information_gain_score(*, hours_to_first_pitch: float | None, volatility: float = 0.0,
                           missing_market_fraction: float = 0.0, recent_signal_change: float = 0.0,
                           new_event_fraction: float = 0.0) -> float:
    proximity = 0.15 if hours_to_first_pitch is None else max(0.0, min(1.0, 1.0 - hours_to_first_pitch / 24.0))
    score = 0.38 * proximity + 0.24 * min(1.0, abs(volatility) / .03) + 0.18 * min(1.0, max(0.0, missing_market_fraction)) + 0.12 * min(1.0, abs(recent_signal_change) / .03) + 0.08 * min(1.0, max(0.0, new_event_fraction))
    return max(0.0, min(1.0, score))


def desired_interval_minutes(hours_to_first_pitch: float | None, volatility: float = 0.0,
                             missing_market_fraction: float = 0.0, recent_signal_change: float = 0.0,
                             new_event_fraction: float = 0.0) -> int:
    # Baseline is driven by game proximity. Signals may only accelerate collection.
    if hours_to_first_pitch is None or hours_to_first_pitch > 24:
        base = 60
    elif hours_to_first_pitch > 12:
        base = 30
    elif hours_to_first_pitch > 6:
        base = 15
    elif hours_to_first_pitch > 2:
        base = 10
    else:
        base = 5
    gain = information_gain_score(hours_to_first_pitch=hours_to_first_pitch, volatility=volatility,
                                  missing_market_fraction=missing_market_fraction,
                                  recent_signal_change=recent_signal_change, new_event_fraction=new_event_fraction)
    if gain >= .72:
        return min(base, 5)
    if gain >= .52:
        return min(base, 10)
    if gain >= .32:
        return min(base, 15)
    return base


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
    hours = ((first - now).total_seconds() / 3600.0) if first else None
    gain = information_gain_score(hours_to_first_pitch=hours, volatility=volatility,
                                  missing_market_fraction=missing_market_fraction,
                                  recent_signal_change=recent_signal_change, new_event_fraction=new_event_fraction)
    interval = desired_interval_minutes(hours, volatility, missing_market_fraction, recent_signal_change, new_event_fraction)
    if force_reason:
        return PullDecision(True, force_reason, interval, now.isoformat(), 1.0, gain)
    if last_pull_at is None:
        return PullDecision(True, 'NO_PRIOR_PULL', interval, now.isoformat(), 1.0, gain)
    last = _dt(last_pull_at)
    due_dt = datetime.fromtimestamp(last.timestamp() + interval * 60, tz=timezone.utc)
    should = now >= due_dt
    urgency = max(0.0, min(1.0, (now.timestamp() - last.timestamp()) / max(1.0, interval * 60)))
    return PullDecision(should, 'INFORMATION_GAIN_PULL_DUE' if should else 'HEARTBEAT_ONLY_NOT_DUE', interval, due_dt.isoformat(), urgency, gain)
