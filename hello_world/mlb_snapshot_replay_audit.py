from __future__ import annotations

import json
import os
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

try:
    import slate_date_patch
    slate_date_patch.apply_to_history(history)
except Exception:
    pass

try:
    import signal_score_guard
    signal_score_guard.apply(history)
except Exception:
    pass

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
REPORT_PATH = "runtime_reports/mlb_snapshot_replay_audit_latest.json"
MIN_PARLAY_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
PULL_START_HOUR_ET = int(os.environ.get("INQSI_ALL_SPORTS_PULL_START_HOUR_ET", "1"))
PULL_INTERVAL_MINUTES = int(os.environ.get("INQSI_PULL_INTERVAL_MINUTES", "15"))
SIGNAL_GRADES = {"STRONG_SOLID", "SOLID", "COIN_FLIP", "MLB_STRONG", "MLB_LEAN"}


def _today_et() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _et(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(SLATE_TZ).isoformat() if dt else None


def _slate_start_utc(slate: str) -> datetime:
    day = datetime.fromisoformat(slate).date()
    return datetime.combine(day, time(PULL_START_HOUR_ET, 0), tzinfo=SLATE_TZ).astimezone(timezone.utc)


def _coverage(pulls: List[Dict[str, Any]], slate: str) -> Dict[str, Any]:
    times = sorted([_parse(p.get("pulled_at")) for p in pulls if _parse(p.get("pulled_at"))])
    start = _slate_start_utc(slate)
    latest = times[-1] if times else None
    first = times[0] if times else None
    since_start = [t for t in times if t >= start]
    ref = latest or datetime.now(timezone.utc)
    expected = int((ref - start).total_seconds() // (PULL_INTERVAL_MINUTES * 60)) + 1 if ref >= start else 0
    return {
        "policy": "MLB pulls should start at 1:00 AM ET and continue every 15 minutes.",
        "expectedStartAtUtc": start.isoformat(),
        "expectedStartAtEt": _et(start),
        "firstPullAtUtc": first.isoformat() if first else None,
        "firstPullAtEt": _et(first),
        "latestPullAtUtc": latest.isoformat() if latest else None,
        "latestPullAtEt": _et(latest),
        "actualPullCount": len(times),
        "actualPullCountSinceStart": len(since_start),
        "expectedPullCountSinceStart": expected,
        "missingPullCountSinceStart": max(expected - len(since_start), 0),
        "coverageRatio": round(len(since_start) / expected, 4) if expected else None,
        "coverageComplete": expected > 0 and len(since_start) >= expected,
        "intervalMinutes": PULL_INTERVAL_MINUTES,
    }


def _game_day(game: Dict[str, Any]) -> Optional[str]:
    dt = _parse(game.get("commence_time") or game.get("commenceTime"))
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def _game_id(game: Dict[str, Any]) -> str:
    return str(game.get("game_key") or game.get("game_id") or game.get("id") or "")


def _series_for_game(pulls: List[Dict[str, Any]], target_game: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = _game_id(target_game)
    series: List[Dict[str, Any]] = []
    for pull in pulls:
        for game in pull.get("games") or []:
            if _game_id(game) == key:
                probs = history.book_probs(game)
                if probs:
                    series.append({"pulled_at": pull.get("pulled_at"), "game": game, "probs": probs})
                break
    return series


def _score_game(cumulative_pulls: List[Dict[str, Any]], game: Dict[str, Any], slate: str) -> Optional[Dict[str, Any]]:
    if _game_day(game) != slate:
        return None
    series = _series_for_game(cumulative_pulls, game)
    if not series:
        return None
    home = history.side_signal(series, "home")
    away = history.side_signal(series, "away")
    best = home if float(home.get("score") or 0) >= float(away.get("score") or 0) else away
    selected_side = best.get("side")
    return {
        "gameId": game.get("game_id") or game.get("id"),
        "gameKey": game.get("game_key") or game.get("id"),
        "homeTeam": game.get("home_team"),
        "awayTeam": game.get("away_team"),
        "matchup": f"{game.get('away_team')} at {game.get('home_team')}",
        "commenceTime": game.get("commence_time"),
        "providerSportKey": game.get("provider_sport_key"),
        "selection": game.get("home_team") if selected_side == "home" else game.get("away_team"),
        "selectedSide": selected_side,
        "grade": best.get("grade"),
        "score": best.get("score"),
        "tags": best.get("tags") or [],
        "pullCountForGame": len(series),
        "homeSignal": home,
        "awaySignal": away,
    }


def _identified(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        home = row.get("homeSignal") or {}
        away = row.get("awaySignal") or {}
        for side_name, side in (("home", home), ("away", away)):
            grade = side.get("grade")
            tags = side.get("tags") or []
            if grade in SIGNAL_GRADES or tags:
                out.append({
                    "gameId": row.get("gameId"),
                    "gameKey": row.get("gameKey"),
                    "matchup": row.get("matchup"),
                    "team": row.get("homeTeam") if side_name == "home" else row.get("awayTeam"),
                    "side": side_name,
                    "grade": grade,
                    "score": side.get("score"),
                    "tags": tags,
                    "pullCountForGame": side.get("pullCount"),
                    "probStart": side.get("probStart"),
                    "probLatest": side.get("probLatest"),
                    "delta": side.get("delta"),
                    "bookDivergence": side.get("bookDivergence"),
                    "latestGap": side.get("latestGap"),
                })
    out.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    return out


def build(slate_date: Optional[str] = None, write_file: bool = True) -> Dict[str, Any]:
    slate = slate_date or _today_et()
    pulls = history.query_pulls("mlb", slate, 500)
    snapshots = []
    all_final_rows: List[Dict[str, Any]] = []
    for idx, pull in enumerate(pulls, start=1):
        cumulative = pulls[:idx]
        rows = []
        for game in pull.get("games") or []:
            scored = _score_game(cumulative, game, slate)
            if scored:
                rows.append(scored)
        rows.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
        for rank, row in enumerate(rows, 1):
            row["rankInSnapshot"] = rank
        identified = _identified(rows)
        snapshots.append({
            "snapshotIndex": idx,
            "pulledAtUtc": pull.get("pulled_at"),
            "pulledAtEt": _et(_parse(pull.get("pulled_at"))),
            "pullId": pull.get("pull_id"),
            "gameCount": len(pull.get("games") or []),
            "scoredGameCount": len(rows),
            "identifiedSignalCount": len(identified),
            "strongOrSolidCount": sum(1 for r in identified if r.get("grade") in {"STRONG_SOLID", "SOLID", "MLB_STRONG", "MLB_LEAN"}),
            "rows": rows,
            "identifiedSignals": identified,
        })
        all_final_rows = rows
    final_identified = _identified(all_final_rows)
    proof = {
        "ok": True,
        "proofType": "MLB_SNAPSHOT_REPLAY_AUDIT",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "createdAtEt": datetime.now(SLATE_TZ).isoformat(),
        "sport": "mlb",
        "slate_date": slate,
        "pullCount": len(pulls),
        "minimumParlayPulls": MIN_PARLAY_PULLS,
        "coverage": _coverage(pulls, slate),
        "snapshotCount": len(snapshots),
        "finalScoredGameCount": len(all_final_rows),
        "finalIdentifiedSignalCount": len(final_identified),
        "finalBoard": all_final_rows,
        "finalIdentifiedSignals": final_identified,
        "snapshots": snapshots,
        "policy": "This proof replays every stored MLB 15-minute pull in order. Each snapshot is scored from cumulative pull history available at that moment, before any qualifying-leg filter is applied.",
    }
    if write_file:
        os.makedirs("runtime_reports", exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(proof, f, indent=2, default=str)
            f.write("\n")
    return proof


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
