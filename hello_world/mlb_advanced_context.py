from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from mlb_official_schedule_authority import normalize_team as _official_normalize_team


ADVANCED_CONTEXT_VERSION = "MLB-B1.0-advanced-context-v2-source-provenance"
STATSAPI_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"

_REQUIRED_CONTEXT_KEYS = [
    "fip_xfip",
    "wrc_plus",
    "starter_handedness_splits",
    "confirmed_probable_pitchers",
    "bullpen_fatigue",
    "confirmed_lineups",
    "weather_wind_roof",
    "ballpark_factors",
    "injuries_late_scratches_news",
    "public_betting_handle",
]

_STATSAPI_CACHE: Dict[str, Dict[str, Any]] = {}
_STATSAPI_CACHE_SECONDS = max(60, int(os.environ.get("INQSI_MLB_STATSAPI_CONTEXT_CACHE_SECONDS", "300")))


def _normalize_team(name: Optional[str]) -> str:
    return _official_normalize_team(name)


def _http_get_json(url: str, timeout: int = 12) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "inqsi-mlb-context/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _payload_fingerprint(payload: Any) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _source_provenance(schedule: Dict[str, Any], dataset: str, source_effective_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "provider": "MLB Stats API",
        "endpoint": schedule.get("endpoint"),
        "dataset": dataset,
        "retrievedAtUtc": schedule.get("retrievedAtUtc"),
        "sourceEffectiveAtUtc": source_effective_at,
        "payloadFingerprint": schedule.get("payloadFingerprint"),
    }


def _date_for_statsapi(game_date_et: str) -> str:
    try:
        dt = datetime.strptime(game_date_et, "%Y-%m-%d")
        return dt.strftime("%m/%d/%Y")
    except Exception:
        return datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y")


def _statsapi_schedule(game_date_et: str) -> Dict[str, Any]:
    cached = _STATSAPI_CACHE.get(game_date_et)
    try:
        cached_at = datetime.fromisoformat(
            str((cached or {}).get("retrievedAtUtc") or "").replace("Z", "+00:00")
        ) if (cached or {}).get("retrievedAtUtc") else None
    except Exception:
        cached_at = None
    if cached_at and cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    if cached and cached.get("ok") and cached_at and datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc) <= timedelta(seconds=_STATSAPI_CACHE_SECONDS):
        return cached
    params = {
        "sportId": "1",
        "date": _date_for_statsapi(game_date_et),
        "hydrate": "probablePitcher,venue",
    }
    url = STATSAPI_SCHEDULE_URL + "?" + urllib.parse.urlencode(params)
    try:
        payload = _http_get_json(url)
        retrieved_at = datetime.now(timezone.utc).isoformat()
        out = {
            "ok": True,
            "source_status": "CONNECTED",
            "payload": payload,
            "error": None,
            "endpoint": url,
            "retrievedAtUtc": retrieved_at,
            "payloadFingerprint": _payload_fingerprint(payload),
        }
    except Exception as exc:
        retrieved_at = datetime.now(timezone.utc).isoformat()
        out = {
            "ok": False,
            "source_status": "ERROR",
            "payload": {},
            "error": str(exc),
            "endpoint": url,
            "retrievedAtUtc": retrieved_at,
            "payloadFingerprint": None,
        }
    _STATSAPI_CACHE[game_date_et] = out
    return out


def _match_statsapi_game(
    game_date_et: str,
    home_team: Optional[str],
    away_team: Optional[str],
    official_game_pk: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    schedule = _statsapi_schedule(game_date_et)
    payload = schedule.get("payload") or {}
    target_home = _normalize_team(home_team)
    target_away = _normalize_team(away_team)
    games = [
        game
        for date_row in payload.get("dates") or []
        for game in (date_row.get("games") or [])
        if isinstance(game, dict)
    ]
    # Once official identity is available it is the only permitted join key.
    # Falling through to team names can silently swap Games 1 and 2 of a
    # doubleheader, contaminating the frozen T-45 fundamentals.
    if official_game_pk not in (None, ""):
        exact = [
            game
            for game in games
            if str(game.get("gamePk") or "") == str(official_game_pk)
        ]
        if len(exact) != 1:
            return None
        teams = exact[0].get("teams") or {}
        home = ((teams.get("home") or {}).get("team") or {}).get("name")
        away = ((teams.get("away") or {}).get("team") or {}).get("name")
        return exact[0] if (
            _normalize_team(home) == target_home
            and _normalize_team(away) == target_away
        ) else None

    team_matches: List[Dict[str, Any]] = []
    for game in games:
        teams = game.get("teams") or {}
        home = ((teams.get("home") or {}).get("team") or {}).get("name")
        away = ((teams.get("away") or {}).get("team") or {}).get("name")
        if _normalize_team(home) == target_home and _normalize_team(away) == target_away:
            team_matches.append(game)
    # Team/date identity is permitted only when unique. Same-team
    # doubleheaders intentionally remain unresolved without official gamePk.
    return team_matches[0] if len(team_matches) == 1 else None


def _probable_pitcher_payload(game_date_et: str, game: Dict[str, Any]) -> Dict[str, Any]:
    schedule = _statsapi_schedule(game_date_et)
    matched = _match_statsapi_game(
        game_date_et,
        game.get("home_team"),
        game.get("away_team"),
        game.get("official_game_pk") or game.get("officialGamePk"),
    )
    if not matched:
        return {
            "source_status": "MISSING_FROM_PROVIDER" if schedule.get("ok") else "ERROR",
            "source": "MLB Stats API schedule hydrate=probablePitcher",
            "home_probable_pitcher": None,
            "away_probable_pitcher": None,
            "home_pitcher_id": None,
            "away_pitcher_id": None,
            "game_status": None,
            "reason": "No matching MLB Stats API schedule game found for this odds-provider matchup.",
            "error": schedule.get("error"),
            "sourceProvenance": _source_provenance(schedule, "schedule hydrate=probablePitcher"),
        }
    teams = matched.get("teams") or {}
    home_probable = (teams.get("home") or {}).get("probablePitcher") or {}
    away_probable = (teams.get("away") or {}).get("probablePitcher") or {}
    home_name = home_probable.get("fullName")
    away_name = away_probable.get("fullName")
    source_status = "CONNECTED" if home_name and away_name else "PARTIAL"
    return {
        "source_status": source_status,
        "source": "MLB Stats API schedule hydrate=probablePitcher",
        "home_probable_pitcher": home_name,
        "away_probable_pitcher": away_name,
        "home_pitcher_id": home_probable.get("id"),
        "away_pitcher_id": away_probable.get("id"),
        "game_status": ((matched.get("status") or {}).get("detailedState")),
        "game_pk": matched.get("gamePk"),
        "sourceProvenance": _source_provenance(
            schedule,
            "schedule hydrate=probablePitcher",
        ),
    }


def _venue_payload(game_date_et: str, game: Dict[str, Any]) -> Dict[str, Any]:
    schedule = _statsapi_schedule(game_date_et)
    matched = _match_statsapi_game(
        game_date_et,
        game.get("home_team"),
        game.get("away_team"),
        game.get("official_game_pk") or game.get("officialGamePk"),
    )
    if not matched:
        return {
            "source_status": "MISSING_FROM_PROVIDER",
            "source": "MLB Stats API schedule hydrate=venue",
            "venue_name": None,
            "venue_id": None,
            "sourceProvenance": _source_provenance(schedule, "schedule hydrate=venue"),
        }
    venue = matched.get("venue") or {}
    return {
        "source_status": "CONNECTED" if venue.get("name") else "PARTIAL",
        "source": "MLB Stats API schedule hydrate=venue",
        "venue_name": venue.get("name"),
        "venue_id": venue.get("id"),
        "sourceProvenance": _source_provenance(
            schedule,
            "schedule hydrate=venue",
        ),
    }


def _empty_metric(name: str, required: bool = True, note: Optional[str] = None) -> Dict[str, Any]:
    return {
        "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
        "required_for_advanced_eligibility": required,
        "value": None,
        "note": note or f"{name} requires a dedicated MLB stats/context provider feed; it is not supplied by the odds feed.",
    }


def _closing_line_value(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    movement = (row or {}).get("movement") or {}
    latest_consensus = (row or {}).get("latest_consensus") or {}
    previous_consensus = (row or {}).get("previous_consensus") or {}
    return {
        "source_status": "SCHEMA_CONNECTED_PENDING_CLOSING_SNAPSHOT",
        "source": "15-minute odds snapshots + post-close/final settlement pass",
        "required_for_advanced_eligibility": False,
        "pregame_completeness_eligible": False,
        "postgame_evaluation_only": True,
        "current_latest_consensus": latest_consensus,
        "previous_consensus": previous_consensus,
        "movement": movement,
        "clv_moneyline_points": None,
        "clv_probability_delta": None,
        "beats_close": None,
        "note": "The Odds API supports odds snapshots and scores; CLV becomes final only after a closing snapshot is frozen and settlement grades the game.",
    }


def _odds_validation(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = row or {}
    return {
        "source_status": "CONNECTED",
        "source": "The Odds API 15-minute HOT pull history",
        "prediction_status": row.get("prediction_status"),
        "hot_delta": row.get("hot_delta"),
        "home_delta": row.get("home_delta"),
        "away_delta": row.get("away_delta"),
        "book_agreement": row.get("book_agreement"),
        "spread_signal": row.get("spread_signal"),
        "total_signal": row.get("total_signal"),
        "latest_consensus": row.get("latest_consensus"),
        "previous_consensus": row.get("previous_consensus"),
    }


def build_advanced_context(game_date_et: str, game: Dict[str, Any], row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    probable = _probable_pitcher_payload(game_date_et, game)
    venue = _venue_payload(game_date_et, game)

    context = {
        "version": ADVANCED_CONTEXT_VERSION,
        "game_date_et": game_date_et,
        "game_key": game.get("game_key") or (row or {}).get("game_key"),
        "home_team": game.get("home_team") or (row or {}).get("home_team"),
        "away_team": game.get("away_team") or (row or {}).get("away_team"),
        "odds_validation": _odds_validation(row),
        "confirmed_probable_pitchers": probable,
        "venue": venue,
        "fip_xfip": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_starter_fip": None,
            "home_starter_xfip": None,
            "away_starter_fip": None,
            "away_starter_xfip": None,
            "home_starter_era": None,
            "away_starter_era": None,
            "home_starter_xera": None,
            "away_starter_xera": None,
            "home_starter_k_minus_bb_pct": None,
            "away_starter_k_minus_bb_pct": None,
            "home_starter_recent_pitch_count": None,
            "away_starter_recent_pitch_count": None,
            "home_starter_recent_innings": None,
            "away_starter_recent_innings": None,
            "home_starter_health_status": None,
            "away_starter_health_status": None,
            "note": "Requires a pitcher-stat provider such as a licensed stats feed, FanGraphs-style feed, or internal pybaseball/statcast pipeline.",
        },
        "wrc_plus": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_team_wrc_plus": None,
            "away_team_wrc_plus": None,
            "home_wrc_plus_vs_pitcher_hand": None,
            "away_wrc_plus_vs_pitcher_hand": None,
            "note": "Requires offensive team/player split data. The odds feed does not provide wRC+.",
        },
        "starter_handedness_splits": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_starter_hand": None,
            "away_starter_hand": None,
            "home_offense_vs_opp_hand": None,
            "away_offense_vs_opp_hand": None,
            "home_pitch_mix": None,
            "away_pitch_mix": None,
            "home_average_velocity_mph": None,
            "away_average_velocity_mph": None,
            "note": "Probable pitcher names may be available from MLB Stats API, but handedness and opponent split metrics require a stats feed.",
        },
        "bullpen_fatigue": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_bullpen_fatigue_score": None,
            "away_bullpen_fatigue_score": None,
            "home_reliever_usage_1d_3d_5d": None,
            "away_reliever_usage_1d_3d_5d": None,
            "home_available_relievers": None,
            "away_available_relievers": None,
            "home_unavailable_relievers": None,
            "away_unavailable_relievers": None,
            "home_high_leverage_roles": None,
            "away_high_leverage_roles": None,
            "note": "Requires pitcher appearance and pitch-count history.",
        },
        "confirmed_lineups": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_lineup_confirmed": None,
            "away_lineup_confirmed": None,
            "home_lineup_strength_delta": None,
            "away_lineup_strength_delta": None,
            "note": "Requires a confirmed lineup/news provider; the odds feed does not provide batting orders.",
        },
        "weather_wind_roof": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "temperature": None,
            "wind_speed": None,
            "wind_direction": None,
            "precipitation_risk": None,
            "roof_status": None,
            "note": "Requires ballpark coordinates plus weather/roof provider data.",
        },
        "ballpark_factors": {
            "source_status": "PARTIAL" if venue.get("source_status") == "CONNECTED" else "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "venue_name": venue.get("venue_name"),
            "venue_id": venue.get("venue_id"),
            "park_factor_runs": None,
            "park_factor_hr": None,
            "sourceProvenance": venue.get("sourceProvenance"),
            "note": "Venue identity is partially connected through MLB Stats API; park-factor values require a park-factor dataset.",
        },
        "injuries_late_scratches_news": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "home_key_injuries": [],
            "away_key_injuries": [],
            "late_scratch_flags": [],
            "pitcher_change_flag": None,
            "note": "Requires a news/injury/transaction feed.",
        },
        "public_betting_handle": {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "required_for_advanced_eligibility": True,
            "bet_pct_home": None,
            "bet_pct_away": None,
            "handle_pct_home": None,
            "handle_pct_away": None,
            "public_side": None,
            "reverse_line_movement_flag": None,
            "note": "Requires public betting splits/handle provider data; The Odds API price feed alone is not handle data.",
        },
        "closing_line_value": _closing_line_value(row),
    }

    blocked = []
    for key in _REQUIRED_CONTEXT_KEYS:
        item = context.get(key) or {}
        if key == "confirmed_probable_pitchers":
            if item.get("source_status") != "CONNECTED":
                blocked.append(key)
            continue
        if item.get("source_status") != "CONNECTED":
            blocked.append(key)

    context["advanced_eligibility"] = {
        "eligible": len(blocked) == 0,
        "required_fields": list(_REQUIRED_CONTEXT_KEYS),
        "blocked_missing_or_pending": blocked,
        "policy": "A leg is not ADVANCED_ELIGIBLE until all required context fields are connected and populated. Market-only picks may still be shown separately.",
    }
    return context


def enrich_row_with_advanced_context(game_date_et: str, row: Dict[str, Any], latest_game: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    game = latest_game or {
        "game_key": row.get("game_key"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
    }
    context = build_advanced_context(game_date_et, game, row)
    return {
        **row,
        "advanced_context": context,
        "advanced_eligible": bool((context.get("advanced_eligibility") or {}).get("eligible")),
        "advanced_blockers": (context.get("advanced_eligibility") or {}).get("blocked_missing_or_pending", []),
    }


def advanced_context_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "version": ADVANCED_CONTEXT_VERSION,
        "required_for_full_algorithm": list(_REQUIRED_CONTEXT_KEYS),
        "source_status": {
            "odds_15_min_pull_history": "CONNECTED",
            "scores_settlement": "CONNECTED",
            "confirmed_probable_pitchers": "PARTIAL_MLB_STATS_API_NO_KEY",
            "venue": "PARTIAL_MLB_STATS_API_NO_KEY",
            "fip_xfip": "NOT_CONNECTED_SOURCE_REQUIRED",
            "wrc_plus": "NOT_CONNECTED_SOURCE_REQUIRED",
            "starter_handedness_splits": "NOT_CONNECTED_SOURCE_REQUIRED",
            "bullpen_fatigue": "NOT_CONNECTED_SOURCE_REQUIRED",
            "confirmed_lineups": "NOT_CONNECTED_SOURCE_REQUIRED",
            "weather_wind_roof": "NOT_CONNECTED_SOURCE_REQUIRED",
            "ballpark_factors": "NOT_CONNECTED_SOURCE_REQUIRED",
            "injuries_late_scratches_news": "NOT_CONNECTED_SOURCE_REQUIRED",
            "public_betting_handle": "NOT_CONNECTED_SOURCE_REQUIRED",
            "closing_line_value": "POSTGAME_EVALUATION_ONLY_NOT_A_PREGAME_REQUIREMENT",
        },
        "odds_api_scope": {
            "usable_for": ["odds", "15-minute movement", "book agreement", "market validation", "scores/final settlement", "CLV once closing snapshots are frozen"],
            "not_a_source_for": ["FIP", "xFIP", "wRC+", "confirmed lineups", "injuries/news", "weather", "public betting handle"],
        },
        "eligibility_policy": "Advanced MLB eligibility is blocked until every required pregame context source is connected. Closing-line value is evaluated only after the game and is never a pregame completeness input.",
    }
