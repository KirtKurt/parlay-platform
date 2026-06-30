from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import sportsdataio_client as sportsdataio

SLATE_TZ = ZoneInfo("America/New_York")


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _season() -> int:
    return datetime.now(SLATE_TZ).year


def _date_days_ago(days: int) -> str:
    return (datetime.now(SLATE_TZ).date() - timedelta(days=days)).isoformat()


def _float(row: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        try:
            value = row.get(key)
            if value is not None and value != "":
                return float(value)
        except Exception:
            continue
    return default


def _int(row: Dict[str, Any], *keys: str, default: int = 0) -> int:
    return int(round(_float(row, *keys, default=float(default))))


def _text(row: Dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _team_key(row: Dict[str, Any]) -> str:
    for key in ("Key", "Team", "TeamID", "GlobalTeamID", "Name"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "UNKNOWN"


def _player_key(row: Dict[str, Any]) -> str:
    for key in ("PlayerID", "GlobalPlayerID", "FantasyDataPlayerID", "Name", "ShortName"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "UNKNOWN"


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _unwrap(payload: Any) -> Tuple[bool, Any, Dict[str, Any]]:
    if isinstance(payload, dict) and "data" in payload and "ok" in payload:
        return bool(payload.get("ok")), payload.get("data"), {k: v for k, v in payload.items() if k != "data"}
    if isinstance(payload, dict) and payload.get("ok") is False:
        return False, None, payload
    return True, payload, {}


def _as_list(payload: Any) -> List[Dict[str, Any]]:
    ok, data, _meta = _unwrap(payload)
    if not ok:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("Games", "TeamSeasonStats", "PlayerSeasonStats", "Players", "StartingLineups", "Lineups", "BoxScores"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _is_pitcher(row: Dict[str, Any]) -> bool:
    pos = _text(row, "Position", "PositionCategory", "FantasyPosition", default="").upper()
    return pos in {"P", "SP", "RP", "PITCHER"} or _float(row, "InningsPitchedDecimal", "InningsPitched", "PitchingInningsPitched", default=0.0) > 0


def _innings(row: Dict[str, Any]) -> float:
    if row.get("OutsPitched") is not None:
        return round(_float(row, "OutsPitched") / 3.0, 2)
    if row.get("InningsPitchedDecimal") is not None:
        return _float(row, "InningsPitchedDecimal")
    raw = row.get("InningsPitched") or row.get("PitchingInningsPitched")
    if raw is None:
        return 0.0
    try:
        value = float(raw)
        whole = int(value)
        frac = round(value - whole, 2)
        if frac in {0.1, 0.2}:
            return round(whole + (1 if frac == 0.1 else 2) / 3.0, 2)
        return value
    except Exception:
        return 0.0


def _team_from_stat(row: Dict[str, Any]) -> str:
    return _text(row, "Team", "TeamKey", "TeamID", "GlobalTeamID", default="")


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


def _starter_identity(game: Dict[str, Any], side: str) -> Dict[str, Any]:
    prefix = "Home" if side == "home" else "Away"
    return {
        "id": _text(game, f"{prefix}StartingPitcherID", f"{prefix}ProbablePitcherID", f"{prefix}PitcherID", default=""),
        "name": _text(game, f"{prefix}StartingPitcher", f"{prefix}ProbablePitcher", f"{prefix}Pitcher", default=""),
        "confirmed": bool(game.get(f"{prefix}StartingPitcherID") or game.get(f"{prefix}StartingPitcher")),
    }


def _match_player(stats: List[Dict[str, Any]], identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    wanted_id = str(identity.get("id") or "").strip()
    wanted_name = str(identity.get("name") or "").lower().strip()
    if not wanted_id and not wanted_name:
        return None
    for row in stats:
        if wanted_id and wanted_id in {str(row.get("PlayerID") or ""), str(row.get("GlobalPlayerID") or ""), str(row.get("FantasyDataPlayerID") or "")}:
            return row
    for row in stats:
        names = {str(row.get("Name") or "").lower().strip(), str(row.get("ShortName") or "").lower().strip()}
        if wanted_name and wanted_name in names:
            return row
    return None


def _starter_score(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {"available": False, "starterScore": 50.0, "reason": "NO_STARTER_STAT_MATCH"}
    era = _float(row, "EarnedRunAverage", "ERA", default=4.5)
    whip = _float(row, "WHIP", "WalksHitsPerInningsPitched", default=1.35)
    strikeouts = _float(row, "Strikeouts", "PitchingStrikeouts", default=0.0)
    walks = _float(row, "Walks", "PitchingWalks", default=0.0)
    innings = max(1.0, _float(row, "InningsPitchedDecimal", "InningsPitched", "PitchingInningsPitched", default=1.0))
    games_started = _float(row, "GamesStarted", "Started", default=0.0)
    k_per_ip = strikeouts / innings
    bb_per_ip = walks / innings
    reliability = min(1.0, innings / max(1.0, games_started * 5.0)) if games_started else min(1.0, innings / 40.0)

    score = 50.0
    score += _clamp((4.50 - era) * 4.0, -10.0, 10.0)
    score += _clamp((1.35 - whip) * 18.0, -8.0, 8.0)
    score += _clamp((k_per_ip - bb_per_ip - 0.55) * 12.0, -8.0, 8.0)
    score += _clamp((reliability - 0.75) * 8.0, -3.0, 3.0)
    return {
        "available": True,
        "playerKey": _player_key(row),
        "name": _text(row, "Name", "ShortName", default=""),
        "era": era,
        "whip": whip,
        "strikeouts": strikeouts,
        "walks": walks,
        "innings": round(innings, 2),
        "gamesStarted": games_started,
        "kPerIp": round(k_per_ip, 3),
        "bbPerIp": round(bb_per_ip, 3),
        "inningsReliability": round(reliability, 3),
        "starterScore": round(max(0.0, min(100.0, score)), 2),
    }


def _bullpen_usage(rows: List[Dict[str, Any]], team_key: str) -> Dict[str, Any]:
    team_key = str(team_key or "").strip()
    relievers = []
    total_ip = 0.0
    total_pitches = 0.0
    for row in rows:
        if not _is_pitcher(row):
            continue
        row_team = _team_from_stat(row)
        if team_key and row_team and str(row_team) != str(team_key):
            continue
        started_flag = str(row.get("Started") or row.get("IsStarter") or "").lower() in {"1", "true", "yes"}
        games_started = _float(row, "GamesStarted", default=0.0)
        if started_flag or games_started > 0:
            continue
        ip = _innings(row)
        pitches = _float(row, "PitchesThrown", "PitchCount", "Pitches", default=0.0)
        if ip <= 0 and pitches <= 0:
            continue
        total_ip += ip
        total_pitches += pitches
        relievers.append({"name": _text(row, "Name", "ShortName", default=""), "innings": round(ip, 2), "pitches": pitches})
    return {"relieverCount": len(relievers), "bullpenInnings": round(total_ip, 2), "bullpenPitches": round(total_pitches, 1), "relievers": relievers[:12]}


def _bullpen_score(usages_by_day: List[Dict[str, Any]]) -> Dict[str, Any]:
    last1 = usages_by_day[0] if usages_by_day else {"bullpenInnings": 0, "bullpenPitches": 0}
    total_ip = sum(float(day.get("bullpenInnings") or 0) for day in usages_by_day)
    total_pitches = sum(float(day.get("bullpenPitches") or 0) for day in usages_by_day)
    score = 50.0
    score -= _clamp(float(last1.get("bullpenInnings") or 0) * 1.2, 0, 10)
    score -= _clamp(total_ip * 0.55, 0, 12)
    score -= _clamp(total_pitches * 0.015, 0, 8)
    return {
        "available": bool(usages_by_day),
        "bullpenScore": round(max(0.0, min(100.0, score)), 2),
        "last1DayUsage": last1,
        "last3DayBullpenInnings": round(total_ip, 2),
        "last3DayBullpenPitches": round(total_pitches, 1),
        "policy": "Lower score means fatigue risk from recent relief innings and pitch count.",
    }


def _lineup_score(game: Dict[str, Any], side: str) -> Dict[str, Any]:
    prefix = "Home" if side == "home" else "Away"
    confirmed = bool(game.get(f"{prefix}LineupConfirmed") or game.get(f"{prefix}StartingLineup") or game.get("LineupsConfirmed"))
    score = 52.0 if confirmed else 48.0
    return {"available": confirmed, "lineupScore": score, "confirmed": confirmed, "policy": "Confirmed lineups slightly increase confidence; unconfirmed lineups apply caution."}


def status(fetch: bool = False) -> Dict[str, Any]:
    base = sportsdataio.status(fetch=fetch)
    base["fundamentalsEngine"] = {
        "ok": True,
        "implementedLayers": ["team_power_rating", "starting_pitcher_score_scaffold", "bullpen_fatigue_scaffold", "lineup_confirmation_scaffold", "combined_fundamentals_preview"],
        "plannedLayers": ["weather_park", "sharp_book_weighting", "error_type_learning", "direct_winner_optimizer_injection"],
        "keyExposed": False,
    }
    return base


def team_power_ratings(season: Optional[int] = None, limit: int = 30) -> Dict[str, Any]:
    season = int(season or _season())
    payload = sportsdataio.team_season_stats(season)
    ok, raw, meta = _unwrap(payload)
    if not ok:
        return {"ok": False, "season": season, "providerMeta": meta, "error": meta.get("error"), "message": meta.get("message")}
    if not isinstance(raw, list):
        return {"ok": False, "season": season, "error": "unexpected_sportsdataio_response", "type": type(raw).__name__, "providerMeta": meta}
    rows = [_power_score(row) for row in raw if isinstance(row, dict)]
    rows.sort(key=lambda r: float(r.get("teamPowerScore") or 0), reverse=True)
    return {
        "ok": True,
        "sport": "mlb",
        "season": season,
        "provider": "SportsDataIO",
        "providerMeta": meta,
        "count": len(rows),
        "ratings": rows[: max(1, min(int(limit or 30), 100))],
        "policy": "Initial team power layer based on win percentage, run differential per game, and home/away splits.",
    }


def _player_season_index(season: int) -> List[Dict[str, Any]]:
    payload = sportsdataio.player_season_stats(season)
    return _as_list(payload)


def _bullpen_rows_last_3_days() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for days in range(1, 4):
        date = _date_days_ago(days)
        out[date] = _as_list(sportsdataio.player_game_stats_by_date(date))
    return out


def _game_team_key(game: Dict[str, Any], side: str) -> str:
    prefix = "Home" if side == "home" else "Away"
    return _text(game, f"{prefix}Team", f"{prefix}TeamID", f"{prefix}TeamKey", default="")


def _game_label(game: Dict[str, Any]) -> str:
    return f"{_game_team_key(game, 'away')} at {_game_team_key(game, 'home')}"


def _side_package(game: Dict[str, Any], side: str, power_by_key: Dict[str, Dict[str, Any]], player_stats: List[Dict[str, Any]], bullpen_by_day: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    team_key = _game_team_key(game, side)
    team_power = power_by_key.get(str(team_key), {})
    team_power_score = _float(team_power, "teamPowerScore", default=50.0)
    starter_id = _starter_identity(game, side)
    starter = _starter_score(_match_player(player_stats, starter_id))
    bullpen_days = [_bullpen_usage(rows, team_key) for _date, rows in sorted(bullpen_by_day.items(), reverse=True)]
    bullpen = _bullpen_score(bullpen_days)
    lineup = _lineup_score(game, side)
    total = (
        (team_power_score - 50.0) * 0.35
        + (float(starter.get("starterScore") or 50.0) - 50.0) * 0.45
        + (float(bullpen.get("bullpenScore") or 50.0) - 50.0) * 0.15
        + (float(lineup.get("lineupScore") or 50.0) - 50.0) * 0.05
    )
    return {
        "teamKey": team_key,
        "teamPower": team_power,
        "starterIdentity": starter_id,
        "starter": starter,
        "bullpen": bullpen,
        "lineup": lineup,
        "sideFundamentalsScore": round(total, 2),
    }


def slate_fundamentals_preview(date_yyyy_mm_dd: Optional[str] = None, season: Optional[int] = None) -> Dict[str, Any]:
    date_yyyy_mm_dd = date_yyyy_mm_dd or _today_et()
    season = int(season or _season())
    games_payload = sportsdataio.games_by_date(date_yyyy_mm_dd)
    games_ok, games_raw, games_meta = _unwrap(games_payload)
    power = team_power_ratings(season=season, limit=100)
    power_by_key = {str(row.get("teamKey")): row for row in power.get("ratings") or []}
    player_stats = _player_season_index(season)
    bullpen_by_day = _bullpen_rows_last_3_days()

    if not games_ok:
        return {"ok": False, "date": date_yyyy_mm_dd, "providerMeta": games_meta, "error": games_meta.get("error"), "message": games_meta.get("message")}
    previews: List[Dict[str, Any]] = []
    games = games_raw if isinstance(games_raw, list) else []
    for game in games:
        if not isinstance(game, dict):
            continue
        home = _side_package(game, "home", power_by_key, player_stats, bullpen_by_day)
        away = _side_package(game, "away", power_by_key, player_stats, bullpen_by_day)
        edge = round(float(home.get("sideFundamentalsScore") or 0) - float(away.get("sideFundamentalsScore") or 0), 2)
        previews.append({
            "gameId": game.get("GameID") or game.get("GlobalGameID"),
            "dateTime": game.get("DateTime") or game.get("Day"),
            "matchup": _game_label(game),
            "homeTeam": home.get("teamKey"),
            "awayTeam": away.get("teamKey"),
            "homeFundamentals": home,
            "awayFundamentals": away,
            "fundamentalsEdgeHomeMinusAway": edge,
            "fundamentalsLean": "home" if edge > 1.5 else "away" if edge < -1.5 else "neutral",
            "status": game.get("Status"),
        })
    return {
        "ok": True,
        "sport": "mlb",
        "date": date_yyyy_mm_dd,
        "season": season,
        "provider": "SportsDataIO",
        "providerMeta": games_meta,
        "gameCount": len(previews),
        "teamPowerAvailable": bool(power.get("ok")),
        "playerSeasonStatsAvailable": bool(player_stats),
        "bullpenLookbackDates": sorted(bullpen_by_day.keys(), reverse=True),
        "games": previews,
        "policy": "Combined fundamentals score = team power 35%, starter 45%, bullpen 15%, lineup confirmation 5%. Neutral fallbacks are used when a SportsDataIO feed is not enabled or a field is unavailable.",
    }
