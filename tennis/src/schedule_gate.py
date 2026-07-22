from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from contracts import ceil_to_slot, parse_utc, slate_date_et, utc_iso


@dataclass(frozen=True)
class WindowDecision:
    slate_date_et: str
    state: str
    first_match_at_utc: Optional[str]
    gate_open_at_utc: Optional[str]
    upcoming_match_count: int
    total_match_count: int
    latched: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def group_events_by_slate(
    events: Iterable[Dict[str, Any]], timezone_name: str
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        slate = event.get("slate_date_et") or slate_date_et(
            event.get("commence_time"), timezone_name
        )
        if not slate:
            continue
        row = dict(event)
        row["slate_date_et"] = slate
        grouped.setdefault(str(slate), []).append(row)
    for slate, rows in grouped.items():
        grouped[slate] = sorted(
            rows,
            key=lambda row: (
                parse_utc(row.get("commence_time"))
                or datetime.max.replace(tzinfo=timezone.utc),
                str(row.get("event_id") or ""),
            ),
        )
    return grouped


def evaluate_window(
    slate_date: str,
    events: Iterable[Dict[str, Any]],
    now_utc: datetime,
    *,
    lead_hours: int = 8,
    interval_minutes: int = 15,
    latched_state: Optional[Dict[str, Any]] = None,
) -> WindowDecision:
    current = now_utc.astimezone(timezone.utc)
    starts = sorted(
        start
        for start in (parse_utc(event.get("commence_time")) for event in events or [])
        if start is not None
    )
    upcoming = [start for start in starts if start > current]
    latched = bool((latched_state or {}).get("opened_at_utc"))

    if not starts:
        return WindowDecision(
            slate_date,
            "NO_MATCHES",
            None,
            None,
            0,
            0,
            latched,
        )

    first = starts[0]
    original_first = parse_utc((latched_state or {}).get("first_match_at_utc"))
    gate_first = original_first or first
    gate_open = ceil_to_slot(gate_first - timedelta(hours=lead_hours), interval_minutes)

    if not upcoming:
        state = "COMPLETE"
    elif current >= gate_open:
        state = "ACTIVE"
    else:
        state = "WAITING_FOR_T_MINUS_8H"

    return WindowDecision(
        slate_date,
        state,
        utc_iso(gate_first),
        utc_iso(gate_open),
        len(upcoming),
        len(starts),
        latched,
    )


def upcoming_events(
    events: Iterable[Dict[str, Any]], now_utc: datetime
) -> List[Dict[str, Any]]:
    current = now_utc.astimezone(timezone.utc)
    return [
        dict(event)
        for event in events or []
        if parse_utc(event.get("commence_time")) is not None
        and parse_utc(event.get("commence_time")) > current
    ]
