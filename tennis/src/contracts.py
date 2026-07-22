from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo


SPORT_KEY = "tennis"
SNAPSHOT_SCHEMA_VERSION = "INQSI-TENNIS-MATCH-SNAPSHOT-v1"
RUN_MANIFEST_SCHEMA_VERSION = "INQSI-TENNIS-PULL-RUN-v1"
WINDOW_SCHEMA_VERSION = "INQSI-TENNIS-WINDOW-v1"
FEATURE_SCHEMA_VERSION = "INQSI-TENNIS-ML-FEATURE-v1"
SIGNAL_SCHEMA_VERSION = "INQSI-TENNIS-MARKET-SIGNALS-v1"


def parse_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def slate_date_et(value: Any, timezone_name: str) -> Optional[str]:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    return parsed.astimezone(ZoneInfo(timezone_name)).date().isoformat()


def floor_to_slot(value: datetime, interval_minutes: int = 15) -> datetime:
    current = value.astimezone(timezone.utc)
    minute = (current.minute // interval_minutes) * interval_minutes
    return current.replace(minute=minute, second=0, microsecond=0)


def ceil_to_slot(value: datetime, interval_minutes: int = 15) -> datetime:
    current = value.astimezone(timezone.utc)
    floored = floor_to_slot(current, interval_minutes)
    if current == floored:
        return floored
    return floored + timedelta(minutes=interval_minutes)


def stable_event_id(raw: Dict[str, Any]) -> Optional[str]:
    value = raw.get("id") or raw.get("event_id")
    if not value:
        return None
    return str(value)


def event_fingerprint(event: Dict[str, Any]) -> str:
    material = {
        "event_id": event.get("event_id"),
        "player_a": event.get("player_a"),
        "player_b": event.get("player_b"),
        "commence_time": event.get("commence_time"),
        "tournament_key": event.get("tournament_key"),
    }
    payload = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [ddb_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): ddb_safe(item) for key, item in value.items()}
    return value


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [from_ddb(item) for item in value]
    if isinstance(value, dict):
        return {str(key): from_ddb(item) for key, item in value.items()}
    return value


def sorted_event_ids(events: Iterable[Dict[str, Any]]) -> list[str]:
    return sorted(
        str(event["event_id"])
        for event in events
        if isinstance(event, dict) and event.get("event_id")
    )
