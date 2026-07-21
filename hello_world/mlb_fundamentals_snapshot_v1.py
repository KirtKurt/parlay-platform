from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "MLB-FUNDAMENTALS-SNAPSHOT-v1-timestamped-source-honest"

REQUIRED_GROUPS = [
    "confirmed_probable_pitchers",
    "fip_xfip",
    "wrc_plus",
    "starter_handedness_splits",
    "bullpen_fatigue",
    "confirmed_lineups",
    "weather_wind_roof",
    "ballpark_factors",
    "injuries_late_scratches_news",
    "public_betting_handle",
    "closing_line_value",
]

PREGAME_REQUIRED_GROUPS = [
    group for group in REQUIRED_GROUPS if group != "closing_line_value"
]


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _status(item: Any) -> str:
    return str((item or {}).get("source_status") or "MISSING") if isinstance(item, dict) else "MISSING"


def _value(item: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _f(item.get(key))
        if value is not None:
            return value
    return None


def build(row: Dict[str, Any]) -> Dict[str, Any]:
    context = row.get("advanced_context") or row.get("advancedContext") or {}
    if not context:
        try:
            import mlb_advanced_context
            slate = str(row.get("slate_date") or row.get("slateDateEt") or "")
            game = {
                "game_key": row.get("gameKey") or row.get("game_key"),
                "home_team": row.get("homeTeam") or row.get("home_team"),
                "away_team": row.get("awayTeam") or row.get("away_team"),
            }
            context = mlb_advanced_context.build_advanced_context(slate, game, row)
        except Exception as exc:
            context = {"snapshotBuildError": str(exc)}

    statuses = {group: _status(context.get(group)) for group in REQUIRED_GROUPS}
    connected = [
        group
        for group in PREGAME_REQUIRED_GROUPS
        if statuses.get(group) == "CONNECTED"
    ]
    partial = [group for group, status in statuses.items() if status in {"PARTIAL", "SCHEMA_CONNECTED_PENDING_CLOSING_SNAPSHOT"}]
    missing = [group for group in PREGAME_REQUIRED_GROUPS if group not in connected]

    fip = context.get("fip_xfip") or {}
    wrc = context.get("wrc_plus") or {}
    bullpen = context.get("bullpen_fatigue") or {}
    lineups = context.get("confirmed_lineups") or {}
    weather = context.get("weather_wind_roof") or {}
    park = context.get("ballpark_factors") or {}
    travel = context.get("travel_rest") or {}
    probable = context.get("confirmed_probable_pitchers") or {}
    injuries = context.get("injuries_late_scratches_news") or {}

    numeric = {
        "homeStarterFip": _value(fip, "home_starter_fip") or 0.0,
        "awayStarterFip": _value(fip, "away_starter_fip") or 0.0,
        "homeStarterXfip": _value(fip, "home_starter_xfip") or 0.0,
        "awayStarterXfip": _value(fip, "away_starter_xfip") or 0.0,
        "homeWrcPlus": _value(wrc, "home_team_wrc_plus", "home_wrc_plus_vs_pitcher_hand") or 0.0,
        "awayWrcPlus": _value(wrc, "away_team_wrc_plus", "away_wrc_plus_vs_pitcher_hand") or 0.0,
        "homeBullpenFatigue": _value(bullpen, "home_bullpen_fatigue_score") or 0.0,
        "awayBullpenFatigue": _value(bullpen, "away_bullpen_fatigue_score") or 0.0,
        "homeLineupStrengthDelta": _value(lineups, "home_lineup_strength_delta") or 0.0,
        "awayLineupStrengthDelta": _value(lineups, "away_lineup_strength_delta") or 0.0,
        "parkFactorRuns": _value(park, "park_factor_runs") or 1.0,
        "windOutMph": _value(weather, "wind_out_mph", "wind_speed") or 0.0,
        "homeRestDays": _value(travel, "home_rest_days") or 0.0,
        "awayRestDays": _value(travel, "away_rest_days") or 0.0,
    }

    source_at = row.get("predictionSourcePullAt") or (row.get("slatePredictionLock") or {}).get("latestScoringPullAt")
    return {
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "asOfUtc": source_at,
        "gameId": row.get("gameId") or row.get("id"),
        "sourceStatuses": statuses,
        "connectedGroups": connected,
        "partialGroups": partial,
        "missingGroups": missing,
        "completenessRatio": round(len(connected) / len(PREGAME_REQUIRED_GROUPS), 4) if PREGAME_REQUIRED_GROUPS else 0.0,
        "pregameCompletenessGroups": list(PREGAME_REQUIRED_GROUPS),
        "postgameOnlyGroups": ["closing_line_value"],
        "closingLineValueCountsTowardPregameCompleteness": False,
        "probablePitchers": {
            "home": probable.get("home_probable_pitcher"),
            "away": probable.get("away_probable_pitcher"),
            "homeId": probable.get("home_pitcher_id"),
            "awayId": probable.get("away_pitcher_id"),
            "source": probable.get("source"),
        },
        "venue": context.get("venue") or {},
        "injuryFlags": {
            "home": injuries.get("home_key_injuries") or [],
            "away": injuries.get("away_key_injuries") or [],
            "lateScratches": injuries.get("late_scratch_flags") or [],
            "pitcherChange": injuries.get("pitcher_change_flag"),
        },
        "numericValues": numeric,
        "missingnessIsFeature": True,
        "sourceHonestyPolicy": "Missing fundamentals remain explicitly missing and are never inferred from odds movement.",
        "advancedEligible": not missing,
        "rawContextVersion": context.get("version"),
    }


def enhance_row(row: Dict[str, Any]) -> Dict[str, Any]:
    row["fundamentalsSnapshot"] = row.get("fundamentalsSnapshot") or build(row)
    row["fundamentalsSnapshotVersion"] = VERSION
    row["fundamentalsAvailableCount"] = len((row["fundamentalsSnapshot"] or {}).get("connectedGroups") or [])
    row["fundamentalsMissingCount"] = len((row["fundamentalsSnapshot"] or {}).get("missingGroups") or [])
    return row


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = result.get("predictions") or []
    for row in rows:
        if isinstance(row, dict):
            enhance_row(row)
    result["fundamentalsSnapshot"] = {
        "applied": True,
        "version": VERSION,
        "rowCount": len(rows),
        "advancedEligibleCount": sum(bool((row.get("fundamentalsSnapshot") or {}).get("advancedEligible")) for row in rows if isinstance(row, dict)),
        "sourceHonestyEnabled": True,
    }
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = enhance_result(original(*args, **kwargs))
        if kwargs.get("store") and hasattr(module, "_store_prediction"):
            stored = 0
            errors: List[str] = []
            for row in result.get("predictions") or []:
                try:
                    response = module._store_prediction(row)
                    if isinstance(response, dict) and response.get("ok"):
                        stored += 1
                    else:
                        errors.append(str(response))
                except Exception as exc:
                    errors.append(str(exc))
            result["fundamentalsSnapshotStoredCount"] = stored
            result["fundamentalsSnapshotStoreErrors"] = errors
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED = True
    return module
