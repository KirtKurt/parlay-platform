from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

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
            "player_game_stats_by_date",
        ],
    }
    if fetch and configured():
        teams = get_json("scores", "Teams")
        out["liveCheck"] = {
            "ok": isinstance(teams, list),
            "teamsCount": len(teams) if isinstance(teams, list) else None,
            "error": teams.get("error") if isinstance(teams, dict) else None,
            "message": teams.get("message") if isinstance(teams, dict) else None,
        }
    return out


def teams() -> Any:
    return get_json("scores", "Teams")


def games_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_json("scores", f"GamesByDate/{date_yyyy_mm_dd}")


def standings(season: int) -> Any:
    return get_json("scores", f"Standings/{season}")


def team_season_stats(season: int) -> Any:
    return get_json("stats", f"TeamSeasonStats/{season}")


def player_game_stats_by_date(date_yyyy_mm_dd: str) -> Any:
    return get_json("stats", f"PlayerGameStatsByDate/{date_yyyy_mm_dd}")
