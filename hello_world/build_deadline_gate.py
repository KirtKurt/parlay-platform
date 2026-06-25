from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo("America/New_York")
DEFAULT_HOURS = 2


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _et(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone(SLATE_TZ).isoformat() if value else None


def event_time(row: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt(row.get("commenceTime") or row.get("commence_time"))


def check(rows: List[Dict[str, Any]], hours_before: int = DEFAULT_HOURS) -> Dict[str, Any]:
    times = sorted([event_time(row) for row in rows if event_time(row)])
    if not times:
        return {
            "ok": False,
            "reason": "NO_EVENT_TIME_AVAILABLE",
            "deadlineHoursBeforeFirstEvent": hours_before,
        }
    first = times[0]
    cutoff = first - timedelta(hours=hours_before)
    current = datetime.now(timezone.utc)
    return {
        "ok": True,
        "firstEventAtUtc": first.isoformat(),
        "firstEventAtEt": _et(first),
        "buildDeadlineUtc": cutoff.isoformat(),
        "buildDeadlineEt": _et(cutoff),
        "deadlineHoursBeforeFirstEvent": hours_before,
        "deadlinePassed": current > cutoff,
        "currentUtc": current.isoformat(),
        "currentEt": _et(current),
    }
