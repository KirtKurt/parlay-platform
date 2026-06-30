from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import sportsdataio_client as sportsdataio

SLATE_TZ = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _season() -> int:
    return datetime.now(SLATE_TZ).year


def _float(row: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        try:
            value = row.get(key)
            if value is not None:
                return float(value)
        except Exception:
            continue
    return default


def _team_key(row: Dict[str, Any]) -> str:
    for key in ("Key", "Team", "TeamID", "GlobalTeamID", "Name"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return "UNKNOWN"


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _power_score(row: Dict[str, Any]) -> Dict[str, Any]:
    wins = _float(row, "Wins", "Win", default=0.0)
    losses = _float(row, "Losses", "Loss", default=0.0)
    games = max(1.0, wins + losses)
    win_pct = wins / games
    runs = _float(row, "Runs", "RunsScored", "OffensiveRuns", default=0.0)
    runs_allowed = _float(row, "RunsAllowed", "OpponentRuns", "EarnedRunsAllowed", default=0.0)
    run_diff_per_game = (runs - runs_allowed) / games
    home_wins = _float(row, "HomeWins", default=0.0)
    home_losses = _float(row, "HomeLosses", default=0.0)
    away_wins = _float(row, "AwayWins", default=0.0)
    away_losses = _float(row, "AwayLosses", default=0.0)
    home_games = max(1.0, home_wins + home_losses)
    away_games = max(1.0, away_wins + away_losses)
    home_pct = home_wins / home_games if home_games else win_pct
    away_pct = away_wins / away_games if away_games else win_pct

    score = 50.0
    score += (win_pct - 0.5) * 45.0
    score += _clamp(run_diff_per_game * 5.0, -12.0, 12.0)
    score += _clamp((home_pct - 0.5) * 10.0, -4.0, 4.0)
    score += _clamp((away_pct - 0.5) * 10.0, -4.0, 4.0)
    score = round(max(0.0, min(100.0, score)), 2)
    return {
        "teamKey": _team_key(row),
        "wins": wins,
        "losses": losses,
        "winPct": round(win_pct, 4),
        "runs": runs,
        "runsAllowed": runs_allowed,
        "runDiffPerGame": round(run_diff_per_game, 3),
        "homeWinPct": round(home_pct, 4),
        "awayWinPct": round(away_pct, 4),
        "teamPowerScore": score,
        "source": "SportsDataIO TeamSeasonStats",
    }


def status(fetch: bool = False) -> Dict[str, Any]:
    base = sportsdataio.status(fetch=fetch)
    base["fundamentalsEngine"] = {
        "ok": True,
        "implementedLayers": ["team_power_rating_scaffold"],
        "plannedLayers": ["starting_pitcher_score", "bullpen_score", "lineup_confirmation", "weather_park", "sharp_book_weighting", "error_type_learning"],
        "keyExposed": False,
    }
    return base


def team_power_ratings(season: Optional[int] = None, limit: int = 30) -> Dict[str, Any]:
    season = int(season or _season())
    raw = sportsdataio.team_season_stats(season)
    if isinstance(raw, dict) and raw.get("ok") is False:
        return {"ok": False, "season": season, "error": raw.get("error"), "message": raw.get("message")}
    if not isinstance(raw, list):
        return {"ok": False, "season": season, "error": "unexpected_sportsdataio_response", "type": type(raw).__name__}
    rows = [_power_score(row) for row in raw if isinstance(row, dict)]
    rows.sort(key=lambda r: float(r.get("teamPowerScore") or 0), reverse=True)
    return {
        "ok": True,
        "sport": "mlb",
        "season": season,
        "provider": "SportsDataIO",
        "count": len(rows),
        "ratings": rows[: max(1, min(int(limit or 30), 100))],
        "policy": "Initial team power layer based on win percentage, run differential per game, and home/away splits. This is a scaffold for the broader fundamentals score.",
    }


def slate_fundamentals_preview(date_yyyy_mm_dd: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
    date_yyyy_mm_dd = date_yyyy_mm_dd or _today_et()
    games = sportsdataio.games_by_date(date_yyyy_mm_dd)
    power = team_power_ratings(season=season, limit=100)
    if isinstance(games, dict) and games.get("ok") is False:
        return {"ok": False, "date": date_yyyy_mm_dd, "error": games.get("error"), "message": games.get("message")}
    power_by_key = {str(row.get("teamKey")): row for row in power.get("ratings") or []}
    previews: List[Dict[str, Any]] = []
    if isinstance(games, list):
        for game in games:
            if not isinstance(game, dict):
                continue
            home_key = str(game.get("HomeTeam") or game.get("HomeTeamID") or game.get("HomeTeamKey") or "")
            away_key = str(game.get("AwayTeam") or game.get("AwayTeamID") or game.get("AwayTeamKey") or "")
            home_power = power_by_key.get(home_key, {})
            away_power = power_by_key.get(away_key, {})
            home_score = _float(home_power, "teamPowerScore", default=50.0)
            away_score = _float(away_power, "teamPowerScore", default=50.0)
            previews.append({
                "gameId": game.get("GameID") or game.get("GlobalGameID"),
                "dateTime": game.get("DateTime") or game.get("Day"),
                "homeTeam": home_key,
                "awayTeam": away_key,
                "homeTeamPowerScore": home_score,
                "awayTeamPowerScore": away_score,
                "teamPowerEdge": round(home_score - away_score, 2),
                "status": game.get("Status"),
            })
    return {
        "ok": True,
        "sport": "mlb",
        "date": date_yyyy_mm_dd,
        "provider": "SportsDataIO",
        "gameCount": len(previews),
        "teamPowerAvailable": bool(power.get("ok")),
        "games": previews,
    }
