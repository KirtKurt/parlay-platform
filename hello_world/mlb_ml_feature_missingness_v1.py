from __future__ import annotations

from typing import Any, Dict

from mlb_fundamentals_snapshot_v1 import VERSION as FUNDAMENTALS_VERSION


VERSION = "MLB-ML-FUNDAMENTAL-MISSINGNESS-v1-explicit-source-masks"

GROUP_NAMES = {
    "confirmed_probable_pitchers": "ProbablePitchers",
    "fip_xfip": "FipXfip",
    "wrc_plus": "WrcPlus",
    "starter_handedness_splits": "StarterSplits",
    "bullpen_fatigue": "BullpenFatigue",
    "confirmed_lineups": "ConfirmedLineups",
    "weather_wind_roof": "WeatherRoof",
    "ballpark_factors": "BallparkFactors",
    "injuries_late_scratches_news": "InjuriesNews",
    "public_betting_handle": "PublicBetting",
    "closing_line_value": "ClosingLineValue",
}


def build_masks(snapshot: Any) -> Dict[str, float]:
    """Build masks from explicit source status, never from numeric zero values."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    statuses = snapshot.get("sourceStatuses") if isinstance(snapshot.get("sourceStatuses"), dict) else {}
    masks: Dict[str, float] = {}
    missing_count = 0
    for group, label in GROUP_NAMES.items():
        available = str(statuses.get(group) or "MISSING").upper() == "CONNECTED"
        missing = 0.0 if available else 1.0
        masks[f"fundamental{label}Missing"] = missing
        missing_count += int(missing)

    masks["fundamentalsMissingGroupCount"] = float(missing_count)
    masks["fundamentalsMissingRatio"] = missing_count / float(len(GROUP_NAMES))
    masks["fundamentalPitchingMissing"] = max(
        masks["fundamentalProbablePitchersMissing"],
        masks["fundamentalFipXfipMissing"],
        masks["fundamentalStarterSplitsMissing"],
    )
    masks["fundamentalOffenseLineupMissing"] = max(
        masks["fundamentalWrcPlusMissing"],
        masks["fundamentalConfirmedLineupsMissing"],
    )
    masks["fundamentalGameContextMissing"] = max(
        masks["fundamentalBullpenFatigueMissing"],
        masks["fundamentalWeatherRoofMissing"],
        masks["fundamentalBallparkFactorsMissing"],
        masks["fundamentalInjuriesNewsMissing"],
    )
    return masks


def provenance_is_lock_safe(snapshot: Any, source_at: Any, lock_at: Any, parse_dt) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("version") != FUNDAMENTALS_VERSION:
        return False
    as_of = parse_dt(snapshot.get("asOfUtc"))
    source = parse_dt(source_at)
    lock = parse_dt(lock_at)
    return bool(as_of and source and lock and as_of <= source <= lock)
