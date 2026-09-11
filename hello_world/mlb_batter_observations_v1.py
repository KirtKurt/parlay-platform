"""Optional, passive per-batter observations from an already-verified Preview feed.

These are current seasonStats as observed, not historical point-in-time
reconstructions, rolling windows, wRC+, or a lineup-strength score. The caller
must bind them to its existing exact-game pre-cutoff source receipt.
"""
from __future__ import annotations

import math
from typing import Any

VERSION = "MLB-LINEUP-SEASON-BATTING-v1-passive-observations"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def season_observation(player_id: int, batting_slot: int, stats: Any) -> dict[str, Any]:
    """Preserve reported rates and sample size; never fill missing rates with zero."""
    if (isinstance(player_id, bool) or not isinstance(player_id, int) or player_id <= 0
            or isinstance(batting_slot, bool) or not isinstance(batting_slot, int)
            or not 1 <= batting_slot <= 9):
        raise ValueError("verified player identity and batting slot required")
    stats = stats if isinstance(stats, dict) else {}
    observed_pa = _finite(stats.get("plateAppearances"))
    pa = (int(observed_pa) if observed_pa is not None and observed_pa >= 0
          and observed_pa.is_integer() else None)
    rates = {}
    for name, maximum in (("ops", 5), ("obp", 1), ("slg", 4)):
        value = _finite(stats.get(name))
        rates[name] = (value if pa is not None and pa > 0 and value is not None
                       and 0 <= value <= maximum else None)
    return {
        "playerId": player_id,
        "battingSlot": batting_slot,
        "plateAppearances": pa,
        **rates,
        "rateObservationCount": sum(value is not None for value in rates.values()),
        "sampleStatus": "OBSERVED" if pa is not None and pa > 0 else (
            "NO_PLATE_APPEARANCES" if pa == 0 else "SAMPLE_UNAVAILABLE"
        ),
    }
