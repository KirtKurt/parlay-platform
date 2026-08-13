"""Match-day collection windows for the isolated soccer collector.

Fixture discovery is intentionally allowed before a collection window opens so
the system can identify the first kickoff.  Odds, bookmaker, and market
collection for every game on that local match-day starts together ten hours
before the first kickoff on that day.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .canonical import iso_utc, parse_utc


DEFAULT_DAY_TIMEZONE = "America/New_York"
DAY_TIMEZONE = os.getenv("SOCCER_AUTO_DAY_TIMEZONE", DEFAULT_DAY_TIMEZONE)
COLLECTION_LEAD_HOURS = int(os.getenv("SOCCER_AUTO_COLLECTION_LEAD_HOURS", "10"))


@dataclass(frozen=True)
class DailyCollectionWindow:
    match_day: str
    timezone: str
    first_kickoff: str
    opens_at: str
    event_count: int


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown soccer match-day timezone: {name}") from exc


def match_day_for(commence_time: str, timezone_name: str = DAY_TIMEZONE) -> str:
    return parse_utc(commence_time).astimezone(_timezone(timezone_name)).date().isoformat()


def daily_collection_windows(
    events: Iterable[Mapping[str, Any]],
    *,
    timezone_name: str = DAY_TIMEZONE,
    lead_hours: int = COLLECTION_LEAD_HOURS,
) -> dict[str, DailyCollectionWindow]:
    """Return one deterministic window for every local day represented.

    The earliest kickoff is calculated across every active soccer competition,
    not independently per league.  That makes the first soccer game of the day
    the single collection authority for all later games on that day.
    """
    if lead_hours < 0:
        raise ValueError("collection lead hours cannot be negative")
    zone = _timezone(timezone_name)
    grouped: dict[str, list[datetime]] = {}
    for event in events:
        commence_time = event.get("commence_time")
        if not commence_time:
            continue
        kickoff = parse_utc(str(commence_time))
        match_day = kickoff.astimezone(zone).date().isoformat()
        grouped.setdefault(match_day, []).append(kickoff)
    return {
        match_day: DailyCollectionWindow(
            match_day=match_day,
            timezone=timezone_name,
            first_kickoff=iso_utc(min(kickoffs)),
            opens_at=iso_utc(min(kickoffs) - timedelta(hours=lead_hours)),
            event_count=len(kickoffs),
        )
        for match_day, kickoffs in sorted(grouped.items())
    }


def stabilize_daily_collection_windows(
    windows: Mapping[str, DailyCollectionWindow],
    persisted: Iterable[Mapping[str, Any]],
) -> dict[str, DailyCollectionWindow]:
    """Keep an opened match-day boundary monotonic when early games disappear.

    The provider's active-event view naturally stops returning completed games.
    Once the first gated provider call has persisted a daily boundary, later
    planning cycles may move that boundary earlier for a newly discovered game,
    but must never move it later merely because the original first game started
    or completed.
    """
    persisted_by_day = {
        str(row.get("match_day") or row.get("SK") or ""): row
        for row in persisted
        if row
    }
    result = dict(windows)
    for match_day, current in windows.items():
        row = persisted_by_day.get(match_day)
        if not row:
            continue
        persisted_timezone = str(row.get("timezone") or current.timezone)
        if persisted_timezone != current.timezone:
            continue
        persisted_open = (
            row.get("scheduled_open_at")
            or row.get("initial_scheduled_open_at")
            or row.get("opens_at")
        )
        persisted_kickoff = row.get("first_kickoff")
        if not persisted_open or not persisted_kickoff:
            continue
        earliest_open = min(parse_utc(current.opens_at), parse_utc(str(persisted_open)))
        earliest_kickoff = min(
            parse_utc(current.first_kickoff),
            parse_utc(str(persisted_kickoff)),
        )
        result[match_day] = DailyCollectionWindow(
            match_day=match_day,
            timezone=current.timezone,
            first_kickoff=iso_utc(earliest_kickoff),
            opens_at=iso_utc(earliest_open),
            event_count=max(current.event_count, int(row.get("event_count") or 0)),
        )
    return result


def collection_status(
    event: Mapping[str, Any],
    windows: Mapping[str, DailyCollectionWindow],
    *,
    observed_at: str | datetime,
    timezone_name: str = DAY_TIMEZONE,
) -> dict[str, Any]:
    match_day = match_day_for(str(event["commence_time"]), timezone_name)
    window = windows.get(match_day)
    if window is None:
        return {
            "match_day": match_day,
            "timezone": timezone_name,
            "open": False,
            "reason": "MATCH_DAY_WINDOW_UNAVAILABLE",
        }
    observed = parse_utc(observed_at)
    opens = parse_utc(window.opens_at)
    return {
        "match_day": match_day,
        "timezone": timezone_name,
        "first_kickoff": window.first_kickoff,
        "opens_at": window.opens_at,
        "event_count": window.event_count,
        "open": observed >= opens,
        "reason": "COLLECTION_WINDOW_OPEN" if observed >= opens else "BEFORE_DAILY_T_MINUS_10_WINDOW",
    }
