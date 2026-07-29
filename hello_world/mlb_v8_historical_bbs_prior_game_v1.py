"""Leakage-safe historical BigBallsData prior-game features for MLB V8.

This module never uses the target game's score, target-game box score, or a same-day
result. It derives team form only from BBD games completed on strictly earlier slate
dates, making the output safe to attach to the immutable T-45 historical record.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "MLB-V8-HISTORICAL-BBS-PRIOR-GAME-v1"
MIN_PRIOR_GAMES = 5
MAX_RECENT_GAMES = 30
FINAL_STATUSES = {"finished", "final", "completed", "closed", "settled"}

TEAM_ALIASES = {
    "oakland athletics": "athletics",
    "oakland a's": "athletics",
    "a's": "athletics",
    "az diamondbacks": "arizona diamondbacks",
    "la angels": "los angeles angels",
    "la dodgers": "los angeles dodgers",
    "ny mets": "new york mets",
    "ny yankees": "new york yankees",
    "sd padres": "san diego padres",
    "sf giants": "san francisco giants",
    "tb rays": "tampa bay rays",
}


def _team(value: Any) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("display_name")
            or value.get("displayName")
            or value.get("team_name")
            or value.get("teamName")
            or value.get("name")
        )
    text = " ".join(str(value or "").strip().lower().split())
    return TEAM_ALIASES.get(text, text)


def _nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _first_number(mapping: Mapping[str, Any], names: Iterable[str]) -> Optional[float]:
    for name in names:
        value = _number(mapping.get(name))
        if value is not None:
            return value
    return None


def score_pair(row: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    """Return final home/away runs from documented and observed BBD shapes."""
    candidates = (
        row.get("score"),
        row.get("scores"),
        _nested(row, "scores", "value"),
        row.get("finalScore"),
        row.get("final_score"),
        _nested(row, "linescore", "totals"),
        row.get("totals"),
    )
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        home = _first_number(
            candidate,
            ("home", "homeScore", "home_score", "homeRuns", "home_runs"),
        )
        away = _first_number(
            candidate,
            ("away", "awayScore", "away_score", "awayRuns", "away_runs"),
        )
        if home is not None and away is not None:
            return home, away
    home = _first_number(
        row,
        ("homeScore", "home_score", "homeRuns", "home_runs", "home_total"),
    )
    away = _first_number(
        row,
        ("awayScore", "away_score", "awayRuns", "away_runs", "away_total"),
    )
    if home is not None and away is not None:
        return home, away
    return None


def _kickoff(row: Mapping[str, Any]) -> Optional[datetime]:
    value = (
        row.get("kickoff_utc")
        or row.get("kickoffUtc")
        or row.get("startTime")
        or row.get("commenceTime")
        or row.get("scheduledAt")
    )
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _provider_id(row: Mapping[str, Any]) -> str:
    return str(
        row.get("match_id")
        or row.get("matchId")
        or row.get("id")
        or row.get("eventId")
        or ""
    ).strip()


@dataclass(frozen=True)
class CompletedGame:
    provider_id: str
    game_day: str
    kickoff_utc: str
    home_team: str
    away_team: str
    home_runs: float
    away_runs: float


def completed_game(row: Mapping[str, Any]) -> Optional[CompletedGame]:
    kickoff = _kickoff(row)
    scores = score_pair(row)
    home = _team(row.get("home") or row.get("home_team") or row.get("homeTeam"))
    away = _team(row.get("away") or row.get("away_team") or row.get("awayTeam"))
    status = str(row.get("status") or "").strip().lower()
    is_final = status in FINAL_STATUSES or row.get("isFinal") is True
    if not is_final or kickoff is None or scores is None or not home or not away:
        return None
    provider_id = _provider_id(row)
    if not provider_id:
        material = {
            "kickoff": kickoff.isoformat(),
            "home": home,
            "away": away,
            "scores": scores,
        }
        provider_id = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return CompletedGame(
        provider_id=provider_id,
        game_day=kickoff.date().isoformat(),
        kickoff_utc=kickoff.isoformat(),
        home_team=home,
        away_team=away,
        home_runs=float(scores[0]),
        away_runs=float(scores[1]),
    )


@dataclass(frozen=True)
class TeamGame:
    game_day: str
    won: int
    runs_for: float
    runs_against: float
    venue: str


def build_team_ledger(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[TeamGame]]:
    """Build a deduplicated BBD team ledger from completed provider games."""
    games: Dict[str, CompletedGame] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        game = completed_game(raw)
        if game is not None:
            games.setdefault(game.provider_id, game)
    ledger: Dict[str, List[TeamGame]] = {}
    for game in sorted(games.values(), key=lambda value: (value.game_day, value.kickoff_utc, value.provider_id)):
        home_won = int(game.home_runs > game.away_runs)
        away_won = int(game.away_runs > game.home_runs)
        ledger.setdefault(game.home_team, []).append(
            TeamGame(
                game_day=game.game_day,
                won=home_won,
                runs_for=game.home_runs,
                runs_against=game.away_runs,
                venue="home",
            )
        )
        ledger.setdefault(game.away_team, []).append(
            TeamGame(
                game_day=game.game_day,
                won=away_won,
                runs_for=game.away_runs,
                runs_against=game.home_runs,
                venue="away",
            )
        )
    return ledger


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _streak(values: Sequence[TeamGame]) -> int:
    if not values:
        return 0
    direction = 1 if values[-1].won else -1
    count = 0
    for game in reversed(values):
        if (1 if game.won else -1) != direction:
            break
        count += 1
    return direction * count


def summarize_team(
    ledger: Mapping[str, Sequence[TeamGame]],
    team_name: str,
    *,
    target_day: str,
    venue: str,
) -> Tuple[Dict[str, float], List[str]]:
    """Summarize strictly earlier BBD games for one target team."""
    team = _team(team_name)
    prior = [
        value
        for value in ledger.get(team, ())
        if str(value.game_day) < str(target_day)
    ][-MAX_RECENT_GAMES:]
    errors: List[str] = []
    if len(prior) < MIN_PRIOR_GAMES:
        errors.append(f"{venue}_bbs_prior_game_floor_not_met")
    recent5 = prior[-5:]
    recent10 = prior[-10:]
    recent30 = prior[-30:]
    venue_games = [value for value in prior if value.venue == venue][-10:]
    last_day = date.fromisoformat(prior[-1].game_day) if prior else None
    target = date.fromisoformat(target_day)
    rest_days = min(max((target - last_day).days if last_day else 3, 0), 10)

    def win_rate(values: Sequence[TeamGame]) -> Optional[float]:
        return _mean([float(value.won) for value in values])

    def runs_for(values: Sequence[TeamGame]) -> Optional[float]:
        return _mean([value.runs_for for value in values])

    def runs_against(values: Sequence[TeamGame]) -> Optional[float]:
        return _mean([value.runs_against for value in values])

    result: Dict[str, float] = {
        "bbsHistoryGames": float(len(prior)),
        "bbsHistoryCoverage": min(len(prior), MAX_RECENT_GAMES) / float(MAX_RECENT_GAMES),
        "bbsStreakNormalized": max(-10, min(10, _streak(prior))) / 10.0,
        "bbsRestDaysNormalized": rest_days / 7.0,
    }
    optional = {
        "bbsWinRate5": win_rate(recent5),
        "bbsWinRate10": win_rate(recent10),
        "bbsWinRate30": win_rate(recent30),
        "bbsRunsForPerGame10": runs_for(recent10),
        "bbsRunsAgainstPerGame10": runs_against(recent10),
        "bbsRunDiffPerGame5": _mean(
            [value.runs_for - value.runs_against for value in recent5]
        ),
        "bbsRunDiffPerGame10": _mean(
            [value.runs_for - value.runs_against for value in recent10]
        ),
        "bbsVenueWinRate10": win_rate(venue_games),
    }
    for key, value in optional.items():
        if value is not None:
            result[key] = float(value)
    return result, errors


def derive_game_features(
    ledger: Mapping[str, Sequence[TeamGame]],
    canonical_game: Mapping[str, Any],
) -> Dict[str, Any]:
    target_day = str(canonical_game.get("slateDateEt") or "")
    if not target_day:
        return {
            "trainingEligible": False,
            "eligibilityErrors": ["target_slate_date_missing"],
            "home": {},
            "away": {},
        }
    home, home_errors = summarize_team(
        ledger,
        str(canonical_game.get("homeTeam") or ""),
        target_day=target_day,
        venue="home",
    )
    away, away_errors = summarize_team(
        ledger,
        str(canonical_game.get("awayTeam") or ""),
        target_day=target_day,
        venue="away",
    )
    errors = sorted(set(home_errors + away_errors))
    return {
        "version": VERSION,
        "targetSlateDateEt": target_day,
        "historyBoundary": "strictly_prior_bbd_completed_slate_dates",
        "sameDayResultsExcluded": True,
        "targetGameOutcomeUsed": False,
        "selectionUsedOutcomes": False,
        "priorCompletedGamesUsed": True,
        "home": home,
        "away": away,
        "trainingEligible": not errors,
        "eligibilityErrors": errors,
    }
