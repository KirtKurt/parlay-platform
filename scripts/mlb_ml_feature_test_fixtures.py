from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_temporal_features_v1 as temporal


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def source_honest_missing_context() -> Dict[str, Any]:
    groups = [
        "confirmed_probable_pitchers", "fip_xfip", "wrc_plus",
        "starter_handedness_splits", "bullpen_fatigue", "confirmed_lineups",
        "weather_wind_roof", "ballpark_factors", "travel_rest",
        "injuries_late_scratches_news", "public_betting_handle", "closing_line_value",
    ]
    return {group: {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"} for group in groups}


def attach_lock_safe_features(row: Dict[str, Any]) -> Dict[str, Any]:
    source_value = (
        row.get("predictionSourcePullAt")
        or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
        or (row.get("lockedCardAudit") or {}).get("explicitSourceAtUtc")
    )
    source = _parse(source_value)
    home_signal = row.setdefault("homeSignal", {})
    away_signal = row.setdefault("awaySignal", {})
    home_latest = float(
        home_signal.get("marketConsensusProbability")
        or home_signal.get("probLatest")
        or 0.55
    )
    points = []
    for index in range(13):
        at = source - timedelta(minutes=15 * (12 - index))
        home = home_latest - (12 - index) * 0.001
        points.append({"pulled_at": at.isoformat(), "fair": {"home": home, "away": 1.0 - home}})
    home_signal["temporalFeatures"] = temporal.summarize_side(points, "home", cutoff_at=source)
    away_signal["temporalFeatures"] = temporal.summarize_side(points, "away", cutoff_at=source)
    row.setdefault("advanced_context", source_honest_missing_context())
    row["fundamentalsSnapshot"] = fundamentals.build(row)
    return row
