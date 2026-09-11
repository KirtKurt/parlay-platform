from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

DEFAULT_MAX_AGE_SECONDS = 180


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def max_age_seconds() -> int:
    try:
        return max(1, int(os.environ.get("ARB_MAX_QUOTE_AGE_SECONDS", str(DEFAULT_MAX_AGE_SECONDS))))
    except ValueError:
        return DEFAULT_MAX_AGE_SECONDS


def assess_quote(quote: Mapping[str, Any], *, now: Optional[datetime] = None, max_age: Optional[int] = None) -> Dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    threshold = int(max_age if max_age is not None else max_age_seconds())
    updated = _parse_iso(quote.get("last_update"))
    if updated is None:
        return {"status": "unknown", "fresh": False, "age_seconds": None, "max_age_seconds": threshold, "reason": "LAST_UPDATE_MISSING_OR_INVALID"}
    age = max(0.0, (now - updated).total_seconds())
    fresh = age <= threshold
    return {
        "status": "fresh" if fresh else "stale",
        "fresh": fresh,
        "age_seconds": round(age, 3),
        "max_age_seconds": threshold,
        "reason": None if fresh else "QUOTE_TOO_OLD",
    }
