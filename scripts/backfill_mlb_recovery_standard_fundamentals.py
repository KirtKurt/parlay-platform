#!/usr/bin/env python3
"""Enrich a T-minus-45 MLB replay with official standard baseball statistics.

The enrichment is research-only. Every numeric statistic is aggregated from MLB
Stats API game logs dated strictly before the game date. Missing values remain
null and are paired with explicit masks. Historical probable-pitcher identities
may reflect later official schedule resolution, so this backfill is never valid
promotion evidence; prospective T-minus-45 capture remains mandatory.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = "MLB-RECOVERY-STANDARD-FUNDAMENTALS-v1-official-game-log-prior-date-only"
BASE = "https://statsapi.mlb.com/api"
USER_AGENT = "inqsi-mlb-recovery-fundamentals/1.0"


class FundamentalsBackfillError(RuntimeError):
    pass


def _number(value: Any) -> Optional[float]:
    if value in (None, "", ".---", "-.--", "-.-"):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _fetch_json(path: str, params: Mapping[str, Any], attempts: int = 4) -> Dict[str, Any]:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={"accept": "application/json", "user-agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    raise FundamentalsBackfillError(f"Stats API request failed: {url}: {last_error}")


def _all_splits(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for block in payload.get("stats") or []:
        if not isinstance(block, dict):
            continue
        rows.extend(row for row in (block.get("splits") or []) if isinstance(row, dict))
    return rows


def _split_date(row: Mapping[str, Any]) -> Optional[date]:
    try:
        return date.fromisoformat(str(row.get("date") or ""))
    except Exception:
        return None


def _before(rows: Sequence[Mapping[str, Any]], game_date: date, days: Optional[int] = None) -> List[Mapping[str, Any]]:
    start = game_date - timedelta(days=days) if days else None
    selected = []
    for row in rows:
        observed = _split_date(row)
        if observed is None or observed >= game_date:
            continue
        if start is not None and observed < start:
            continue
        selected.append(row)
    return selected


def _hitting(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    totals = defaultdict(float)
    for row in rows:
        stat = row.get("stat") or {}
        for key in (
            "gamesPlayed", "atBats", "plateAppearances", "hits", "doubles", "triples",
            "homeRuns", "baseOnBalls", "hitByPitch", "strikeOuts", "sacFlies",
            "runs", "totalBases", "leftOnBase",
        ):
            totals[key] += _number(stat.get(key)) or 0.0
    games = totals["gamesPlayed"]
    at_bats = totals["atBats"]
    pa = totals["plateAppearances"]
    hits = totals["hits"]
    walks = totals["baseOnBalls"]
    hbp = totals["hitByPitch"]
    sac_flies = totals["sacFlies"]
    obp_den = at_bats + walks + hbp + sac_flies
    avg = hits / at_bats if at_bats else None
    obp = (hits + walks + hbp) / obp_den if obp_den else None
    slg = totals["totalBases"] / at_bats if at_bats else None
    return {
        "games": int(games),
        "avg": avg,
        "obp": obp,
        "slg": slg,
        "ops": (obp + slg) if obp is not None and slg is not None else None,
        "runsPerGame": totals["runs"] / games if games else None,
        "homeRunsPerGame": totals["homeRuns"] / games if games else None,
        "walkRate": walks / pa if pa else None,
        "strikeoutRate": totals["strikeOuts"] / pa if pa else None,
        "leftOnBasePerGame": totals["leftOnBase"] / games if games else None,
    }


def _pitching(rows: Sequence[Mapping[str, Any]], starters_only: bool = False) -> Dict[str, Any]:
    filtered = []
    for row in rows:
        stat = row.get("stat") or {}
        if starters_only and _integer(stat.get("gamesStarted")) <= 0:
            continue
        filtered.append(row)
    totals = defaultdict(float)
    dates: List[date] = []
    pitches_by_game: List[float] = []
    for row in filtered:
        observed = _split_date(row)
        if observed:
            dates.append(observed)
        stat = row.get("stat") or {}
        for key in (
            "gamesPlayed", "gamesPitched", "gamesStarted", "outs", "earnedRuns", "runs",
            "hits", "baseOnBalls", "homeRuns", "strikeOuts", "battersFaced",
            "numberOfPitches", "hitBatsmen", "blownSaves", "saves", "holds",
        ):
            totals[key] += _number(stat.get(key)) or 0.0
        pitches = _number(stat.get("numberOfPitches"))
        if pitches is not None:
            pitches_by_game.append(pitches)
    innings = totals["outs"] / 3.0
    games = totals["gamesPitched"] or totals["gamesPlayed"]
    starts = totals["gamesStarted"]
    return {
        "games": int(games),
        "starts": int(starts),
        "innings": innings,
        "era": 9.0 * totals["earnedRuns"] / innings if innings else None,
        "runsPer9": 9.0 * totals["runs"] / innings if innings else None,
        "whip": (totals["hits"] + totals["baseOnBalls"]) / innings if innings else None,
        "strikeoutsPer9": 9.0 * totals["strikeOuts"] / innings if innings else None,
        "walksPer9": 9.0 * totals["baseOnBalls"] / innings if innings else None,
        "homeRunsPer9": 9.0 * totals["homeRuns"] / innings if innings else None,
        "strikeoutWalkRatio": totals["strikeOuts"] / totals["baseOnBalls"] if totals["baseOnBalls"] else None,
        "inningsPerStart": innings / starts if starts else None,
        "pitchesPerGame": totals["numberOfPitches"] / games if games else None,
        "averagePitchesLast3": (
            sum(pitches_by_game[-3:]) / len(pitches_by_game[-3:]) if pitches_by_game else None
        ),
        "lastAppearanceDate": max(dates).isoformat() if dates else None,
        "blownSaveRate": totals["blownSaves"] / games if games else None,
    }


def _days_rest(last_date: Any, game_date: date) -> Optional[int]:
    if not last_date:
        return None
    try:
        return (game_date - date.fromisoformat(str(last_date))).days
    except Exception:
        return None


def _diff(home: Any, away: Any, *, lower_is_better: bool = False) -> Optional[float]:
    home_number = _number(home)
    away_number = _number(away)
    if home_number is None or away_number is None:
        return None
    return (away_number - home_number) if lower_is_better else (home_number - away_number)


def _schedule(start_date: str, end_date: str) -> Dict[str, Dict[str, Any]]:
    payload = _fetch_json(
        "/v1/schedule",
        {
            "sportId": 1,
            "startDate": start_date,
            "endDate": end_date,
            "hydrate": "probablePitcher,team,venue",
        },
    )
    out: Dict[str, Dict[str, Any]] = {}
    for date_row in payload.get("dates") or []:
        for game in date_row.get("games") or []:
            if not isinstance(game, dict) or str(game.get("gameType") or "") != "R":
                continue
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            out[str(game.get("gamePk") or "")] = {
                "gamePk": str(game.get("gamePk") or ""),
                "officialDate": str(game.get("officialDate") or date_row.get("date") or ""),
                "homeTeamId": (home.get("team") or {}).get("id"),
                "awayTeamId": (away.get("team") or {}).get("id"),
                "homeTeam": (home.get("team") or {}).get("name"),
                "awayTeam": (away.get("team") or {}).get("name"),
                "homeProbablePitcherId": (home.get("probablePitcher") or {}).get("id"),
                "awayProbablePitcherId": (away.get("probablePitcher") or {}).get("id"),
                "homeProbablePitcher": (home.get("probablePitcher") or {}).get("fullName"),
                "awayProbablePitcher": (away.get("probablePitcher") or {}).get("fullName"),
                "venueId": (game.get("venue") or {}).get("id"),
                "venue": (game.get("venue") or {}).get("name"),
            }
    return out


def _fetch_log(kind: str, entity_id: int, season: int) -> List[Dict[str, Any]]:
    if kind == "team_hitting":
        path = f"/v1/teams/{entity_id}/stats"
        params = {"stats": "gameLog", "group": "hitting", "season": season}
    elif kind == "team_pitching":
        path = f"/v1/teams/{entity_id}/stats"
        params = {"stats": "gameLog", "group": "pitching", "season": season}
    elif kind == "pitcher":
        path = f"/v1/people/{entity_id}/stats"
        params = {"stats": "gameLog", "group": "pitching", "season": season}
    else:
        raise FundamentalsBackfillError(f"unknown log kind: {kind}")
    return _all_splits(_fetch_json(path, params))


def _cache_logs(schedule: Mapping[str, Mapping[str, Any]], season: int) -> Tuple[Dict[Tuple[str, int], List[Dict[str, Any]]], List[Dict[str, Any]]]:
    tasks = set()
    for game in schedule.values():
        for team_id in (game.get("homeTeamId"), game.get("awayTeamId")):
            if team_id:
                tasks.add(("team_hitting", int(team_id)))
                tasks.add(("team_pitching", int(team_id)))
        for pitcher_id in (game.get("homeProbablePitcherId"), game.get("awayProbablePitcherId")):
            if pitcher_id:
                tasks.add(("pitcher", int(pitcher_id)))
    cache: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    failures: List[Dict[str, Any]] = []

    def fetch(task: Tuple[str, int]):
        kind, entity_id = task
        return task, _fetch_log(kind, entity_id, season)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(fetch, task): task for task in sorted(tasks)}
        for future in concurrent.futures.as_completed(future_map):
            task = future_map[future]
            try:
                key, rows = future.result()
                cache[key] = rows
            except Exception as exc:
                failures.append({"kind": task[0], "entityId": task[1], "error": f"{type(exc).__name__}: {exc}"})
    return cache, failures


def _side_context(
    game_date: date,
    team_id: Optional[int],
    pitcher_id: Optional[int],
    cache: Mapping[Tuple[str, int], Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    hitting_log = cache.get(("team_hitting", int(team_id))) if team_id else None
    pitching_log = cache.get(("team_pitching", int(team_id))) if team_id else None
    pitcher_log = cache.get(("pitcher", int(pitcher_id))) if pitcher_id else None
    season_hitting = _hitting(_before(hitting_log or [], game_date)) if hitting_log is not None else {}
    recent_hitting = _hitting(_before(hitting_log or [], game_date, 21)) if hitting_log is not None else {}
    season_staff = _pitching(_before(pitching_log or [], game_date)) if pitching_log is not None else {}
    recent_staff = _pitching(_before(pitching_log or [], game_date, 21)) if pitching_log is not None else {}
    season_starter = _pitching(_before(pitcher_log or [], game_date), starters_only=True) if pitcher_log is not None else {}
    recent_starter = _pitching(_before(pitcher_log or [], game_date, 21), starters_only=True) if pitcher_log is not None else {}
    return {
        "teamId": team_id,
        "probablePitcherId": pitcher_id,
        "seasonHitting": season_hitting,
        "recent21dHitting": recent_hitting,
        "seasonStaffPitching": season_staff,
        "recent21dStaffPitching": recent_staff,
        "seasonStarterPitching": season_starter,
        "recent21dStarterPitching": recent_starter,
        "starterDaysRest": _days_rest(season_starter.get("lastAppearanceDate"), game_date),
        "sourceAvailable": {
            "teamHittingGameLog": hitting_log is not None,
            "teamPitchingGameLog": pitching_log is not None,
            "starterPitchingGameLog": pitcher_log is not None,
        },
    }


def _feature_pair(home: Mapping[str, Any], away: Mapping[str, Any]) -> Dict[str, Any]:
    hs = home.get("seasonHitting") or {}
    as_ = away.get("seasonHitting") or {}
    hr = home.get("recent21dHitting") or {}
    ar = away.get("recent21dHitting") or {}
    hp = home.get("seasonStaffPitching") or {}
    ap = away.get("seasonStaffPitching") or {}
    hpr = home.get("recent21dStaffPitching") or {}
    apr = away.get("recent21dStaffPitching") or {}
    hsp = home.get("seasonStarterPitching") or {}
    asp = away.get("seasonStarterPitching") or {}
    hsr = home.get("recent21dStarterPitching") or {}
    asr = away.get("recent21dStarterPitching") or {}
    features = {
        "offenseOpsGapHome": _diff(hs.get("ops"), as_.get("ops")),
        "offenseRunsPerGameGapHome": _diff(hs.get("runsPerGame"), as_.get("runsPerGame")),
        "offenseWalkRateGapHome": _diff(hs.get("walkRate"), as_.get("walkRate")),
        "offenseStrikeoutRateGapHome": _diff(hs.get("strikeoutRate"), as_.get("strikeoutRate"), lower_is_better=True),
        "recentOffenseOpsGapHome": _diff(hr.get("ops"), ar.get("ops")),
        "recentOffenseRunsPerGameGapHome": _diff(hr.get("runsPerGame"), ar.get("runsPerGame")),
        "staffEraGapHome": _diff(hp.get("era"), ap.get("era"), lower_is_better=True),
        "staffWhipGapHome": _diff(hp.get("whip"), ap.get("whip"), lower_is_better=True),
        "staffStrikeoutWalkGapHome": _diff(hp.get("strikeoutWalkRatio"), ap.get("strikeoutWalkRatio")),
        "staffHomeRunsPer9GapHome": _diff(hp.get("homeRunsPer9"), ap.get("homeRunsPer9"), lower_is_better=True),
        "recentStaffEraGapHome": _diff(hpr.get("era"), apr.get("era"), lower_is_better=True),
        "recentStaffWhipGapHome": _diff(hpr.get("whip"), apr.get("whip"), lower_is_better=True),
        "starterEraGapHome": _diff(hsp.get("era"), asp.get("era"), lower_is_better=True),
        "starterWhipGapHome": _diff(hsp.get("whip"), asp.get("whip"), lower_is_better=True),
        "starterStrikeoutWalkGapHome": _diff(hsp.get("strikeoutWalkRatio"), asp.get("strikeoutWalkRatio")),
        "starterHomeRunsPer9GapHome": _diff(hsp.get("homeRunsPer9"), asp.get("homeRunsPer9"), lower_is_better=True),
        "starterInningsPerStartGapHome": _diff(hsp.get("inningsPerStart"), asp.get("inningsPerStart")),
        "starterRecentEraGapHome": _diff(hsr.get("era"), asr.get("era"), lower_is_better=True),
        "starterRecentWhipGapHome": _diff(hsr.get("whip"), asr.get("whip"), lower_is_better=True),
        "starterRecentStrikeoutWalkGapHome": _diff(hsr.get("strikeoutWalkRatio"), asr.get("strikeoutWalkRatio")),
        "starterDaysRestGapHome": _diff(home.get("starterDaysRest"), away.get("starterDaysRest")),
        "homeField": 1.0,
    }
    masks = {f"{key}Missing": value is None for key, value in features.items() if key != "homeField"}
    available = sum(value is not None for key, value in features.items() if key != "homeField")
    total = len(features) - 1
    return {
        "features": features,
        "missingMasks": masks,
        "availableFeatureCount": available,
        "featureCount": total,
        "completenessPct": round(100.0 * available / total, 2) if total else 0.0,
    }


def enrich(replay: Mapping[str, Any]) -> Dict[str, Any]:
    rows = [dict(row) for row in (replay.get("rows") or []) if isinstance(row, dict)]
    if not rows:
        raise FundamentalsBackfillError("replay contains no rows")
    start_date = min(str(row.get("slateDateEt")) for row in rows)
    end_date = max(str(row.get("slateDateEt")) for row in rows)
    season = int(start_date[:4])
    schedule = _schedule(start_date, end_date)
    cache, failures = _cache_logs(schedule, season)
    enriched: List[Dict[str, Any]] = []
    missing_schedule = 0
    for row in rows:
        game = schedule.get(str(row.get("gamePk") or ""))
        if not game:
            missing_schedule += 1
            out = dict(row)
            out["standardFundamentals"] = {
                "version": VERSION,
                "available": False,
                "reason": "OFFICIAL_SCHEDULE_GAME_NOT_FOUND",
            }
            enriched.append(out)
            continue
        game_date = date.fromisoformat(game["officialDate"])
        home = _side_context(
            game_date,
            int(game["homeTeamId"]) if game.get("homeTeamId") else None,
            int(game["homeProbablePitcherId"]) if game.get("homeProbablePitcherId") else None,
            cache,
        )
        away = _side_context(
            game_date,
            int(game["awayTeamId"]) if game.get("awayTeamId") else None,
            int(game["awayProbablePitcherId"]) if game.get("awayProbablePitcherId") else None,
            cache,
        )
        paired = _feature_pair(home, away)
        out = dict(row)
        out["standardFundamentals"] = {
            "version": VERSION,
            "available": paired["availableFeatureCount"] > 0,
            "asOfDate": (game_date - timedelta(days=1)).isoformat(),
            "strictlyPriorGameLogsOnly": True,
            "historicalProbablePitcherIdentityMayReflectPostgameScheduleResolution": True,
            "validForPromotionEvidence": False,
            "officialGamePk": game["gamePk"],
            "homeTeamId": game.get("homeTeamId"),
            "awayTeamId": game.get("awayTeamId"),
            "homeProbablePitcherId": game.get("homeProbablePitcherId"),
            "awayProbablePitcherId": game.get("awayProbablePitcherId"),
            "homeProbablePitcher": game.get("homeProbablePitcher"),
            "awayProbablePitcher": game.get("awayProbablePitcher"),
            "venueId": game.get("venueId"),
            "venue": game.get("venue"),
            "home": home,
            "away": away,
            **paired,
        }
        enriched.append(out)
    completeness = [
        _number((row.get("standardFundamentals") or {}).get("completenessPct")) or 0.0
        for row in enriched
    ]
    output = {
        **{key: value for key, value in replay.items() if key != "rows"},
        "rows": enriched,
        "standardFundamentalsBackfill": {
            "version": VERSION,
            "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": "MLB Stats API",
            "readOnly": True,
            "awsWritesPerformed": False,
            "gameLogDatesStrictlyBeforeGame": True,
            "historicalProbablePitcherIdentityMayReflectPostgameScheduleResolution": True,
            "validForPromotionEvidence": False,
            "rowCount": len(enriched),
            "scheduleGameCount": len(schedule),
            "missingScheduleCount": missing_schedule,
            "logCacheEntryCount": len(cache),
            "logFetchFailureCount": len(failures),
            "logFetchFailures": failures,
            "averageCompletenessPct": round(sum(completeness) / len(completeness), 2),
            "rowsAtLeast75PctComplete": sum(value >= 75.0 for value in completeness),
        },
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    replay = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    output = enrich(replay)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(output["standardFundamentalsBackfill"], indent=2, sort_keys=True))
    if output["standardFundamentalsBackfill"]["logFetchFailureCount"]:
        raise SystemExit("One or more official game-log fetches failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
