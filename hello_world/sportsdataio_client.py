from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

SPORTSDATAIO_API_KEY = os.environ.get("SPORTSDATAIO_API_KEY", "")
BASE_URL = os.environ.get("SPORTSDATAIO_BASE_URL", "https://api.sportsdata.io/v3/mlb").rstrip("/")
TIMEOUT_SECONDS = int(os.environ.get("SPORTSDATAIO_TIMEOUT_SECONDS", "25"))


def configured() -> bool:
    return bool(SPORTSDATAIO_API_KEY.strip())


def _safe_error(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error": type(exc).__name__, "message": str(exc)[:240]}


def _headers() -> Dict[str, str]:
    return {
        "accept": "application/json",
        "Ocp-Apim-Subscription-Key": SPORTSDATAIO_API_KEY,
    }


def _url(area: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    area = area.strip("/")
    path = path.strip("/")
    url = f"{BASE_URL}/{area}/json/{path}"
    if query:
        clean = {k: v for k, v in query.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    return url


def get_json(area: str, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
    if not configured():
        return {"ok": False, "error": "SPORTSDATAIO_API_KEY missing"}
    req = urllib.request.Request(_url(area, path, query), headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return _safe_error(exc)


def get_first_success(candidates: Iterable[Tuple[str, str]]) -> Dict[str, Any]:
    """Try endpoint candidates and return the first list/dict payload that is not an error.

    SportsDataIO access differs by subscription, so this lets the fundamentals
    engine probe equivalent feeds safely without exposing the API key or crashing.
    """
    attempts: List[Dict[str, Any]] = []
    for area, path in candidates:
        result = get_json(area, path)
        if isinstance(result, dict) and result.get("ok") is False:
            attempts.append({"area": area, "path": path, "error": result.get("error"), "message": result.get("message")})
            continue
        return {"ok": True, "area": area, "path": path, "data": result, "attempts": attempts}
    return {"ok": False, "error": "no_candidate_endpoint_succeeded", "attempts": attempts}


def status(fetch: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": True,
        "provider": "sportsdataio",
        "configured": configured(),
        "baseUrl": BASE_URL,
        "keyExposed": False,
        "availableClientMethods": [
            "teams",
            "games_by_date",
            "standings",
            "team_season_stats",
            "player_season_stats",
            "player_game_stats_by_date",
            "box_scores_by_date",
            "starting_lineups_by_date",
        ],
    }
    if fetch and configured():
        teams_payload = get_json("scores", "Teams")
        out["liveCheck"] = {
            "ok": isinstance(teams_payload, list),
            "teamsCount": len(teams_payload) if isinstance(teams_payload, list) else None,
            "error": teams_payload.get("error") if isinstance(teams_payload, dict) else None,
            "message": teams_payload.get("message") if isinstance(teams_payload, dict) else None,
        }
    return out


def teams() -> Any:
    return get_json("scores", "Teams")


def games_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_first_success([
        ("scores", f"GamesByDate/{date_yyyy_mm_dd}"),
        ("scores", f"Games/{date_yyyy_mm_dd}"),
    ])


def standings(season: int) -> Any:
    return get_first_success([
        ("scores", f"Standings/{season}"),
        ("scores", f"StandingsBasic/{season}"),
    ])


def team_season_stats(season: int) -> Any:
    return get_first_success([
        ("stats", f"TeamSeasonStats/{season}"),
        ("stats", f"TeamSeasonStatsBasic/{season}"),
    ])


def player_season_stats(season: int) -> Any:
    return get_first_success([
        ("stats", f"PlayerSeasonStats/{season}"),
        ("stats", f"PlayerSeasonStatsBasic/{season}"),
    ])


def player_game_stats_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_first_success([
        ("stats", f"PlayerGameStatsByDate/{date_yyyy_mm_dd}"),
        ("stats", f"PlayerGameStatsByDateFinal/{date_yyyy_mm_dd}"),
    ])


def box_scores_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_first_success([
        ("stats", f"BoxScores/{date_yyyy_mm_dd}"),
        ("scores", f"BoxScores/{date_yyyy_mm_dd}"),
    ])


def starting_lineups_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_first_success([
        ("stats", f"StartingLineupsByDate/{date_yyyy_mm_dd}"),
        ("scores", f"StartingLineupsByDate/{date_yyyy_mm_dd}"),
        ("stats", f"LineupsByDate/{date_yyyy_mm_dd}"),
    ])
