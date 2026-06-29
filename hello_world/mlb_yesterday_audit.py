from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history
import mlb_game_winner_engine
import mlb_b10_engine

try:
    import mlb_all_games_signal_proof
except Exception:
    mlb_all_games_signal_proof = None

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
REPORT_PATH = "runtime_reports/mlb_yesterday_audit_latest.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def yesterday_et() -> str:
    return (datetime.now(SLATE_TZ).date() - timedelta(days=1)).isoformat()


def normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def slate_date_from_commence(value: Any) -> Optional[str]:
    dt = parse_dt(value)
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def scores_url(days_from: int = 3) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY, "daysFrom": days_from, "dateFormat": "iso"})
    return "https://api.the-odds-api.com/v4/sports/baseball_mlb/scores/?" + query


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def pull_final_scores(slate_date: str, days_from: int = 3) -> Dict[str, Any]:
    raw = http_get_json(scores_url(days_from=days_from))
    finals = []
    by_matchup: Dict[str, Dict[str, Any]] = {}
    for game in raw or []:
        if not game.get("completed"):
            continue
        if slate_date_from_commence(game.get("commence_time")) != slate_date:
            continue
        home = game.get("home_team")
        away = game.get("away_team")
        home_score = away_score = None
        for score in game.get("scores") or []:
            if score.get("name") == home:
                home_score = int(score.get("score"))
            if score.get("name") == away:
                away_score = int(score.get("score"))
        if home_score is None or away_score is None:
            continue
        winner = home if home_score > away_score else away if away_score > home_score else "TIE"
        row = {
            "id": game.get("id"),
            "homeTeam": home,
            "awayTeam": away,
            "matchup": f"{away} at {home}",
            "commenceTime": game.get("commence_time"),
            "homeScore": home_score,
            "awayScore": away_score,
            "winner": winner,
            "margin": abs(home_score - away_score),
            "totalRuns": home_score + away_score,
            "completed": True,
        }
        finals.append(row)
        by_matchup[f"{normalize_team(away)}|{normalize_team(home)}"] = row
    return {"ok": True, "slate_date": slate_date, "finalScoreCount": len(finals), "finalScores": finals, "byMatchup": by_matchup}


def outcome_for(row: Dict[str, Any], score_report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = f"{normalize_team(row.get('awayTeam'))}|{normalize_team(row.get('homeTeam'))}"
    return (score_report.get("byMatchup") or {}).get(key)


def audit_rows(slate_date: str, score_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    predictions = mlb_game_winner_engine.predict_all(slate_date, store=False, limit=500)
    b10 = mlb_b10_engine.build(slate_date)
    b10_by_game = {g.get("gameId"): g for g in (b10.get("legs") or [])}
    rows = []
    for pred in predictions.get("predictions") or []:
        outcome = outcome_for(pred, score_report)
        b10_game = b10_by_game.get(pred.get("gameId")) or {}
        if not outcome:
            rows.append({
                "gameId": pred.get("gameId"),
                "matchup": f"{pred.get('awayTeam')} at {pred.get('homeTeam')}",
                "status": "MISSING_FINAL_SCORE",
                "predictedWinner": pred.get("predictedWinner"),
                "gameWinnerScore": pred.get("score"),
                "b10SelectedTeam": b10_game.get("selection"),
            })
            continue
        actual = outcome.get("winner")
        gw_correct = normalize_team(pred.get("predictedWinner")) == normalize_team(actual)
        b10_correct = normalize_team(b10_game.get("selection")) == normalize_team(actual) if b10_game else None
        rows.append({
            "gameId": pred.get("gameId"),
            "matchup": outcome.get("matchup"),
            "status": "FINAL",
            "homeScore": outcome.get("homeScore"),
            "awayScore": outcome.get("awayScore"),
            "actualWinner": actual,
            "predictedWinner": pred.get("predictedWinner"),
            "predictedSide": pred.get("predictedSide"),
            "gameWinnerCorrect": gw_correct,
            "gameWinnerScore": pred.get("score"),
            "gameWinnerConfidenceTier": pred.get("confidenceTier"),
            "gameWinnerTags": pred.get("tags") or [],
            "b10SelectedTeam": b10_game.get("selection"),
            "b10SelectedSide": b10_game.get("selectedSide"),
            "b10SelectedGrade": b10_game.get("grade"),
            "b10SelectedScore": b10_game.get("score"),
            "b10Correct": b10_correct,
            "b10Tags": b10_game.get("tags") or [],
        })
    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    final_rows = [r for r in rows if r.get("status") == "FINAL"]
    gw_correct = [r for r in final_rows if r.get("gameWinnerCorrect") is True]
    b10_known = [r for r in final_rows if r.get("b10Correct") is not None]
    b10_correct = [r for r in b10_known if r.get("b10Correct") is True]
    return {
        "auditedGameCount": len(rows),
        "finalGameCount": len(final_rows),
        "missingFinalScoreCount": len(rows) - len(final_rows),
        "gameWinnerCorrect": len(gw_correct),
        "gameWinnerWrong": len(final_rows) - len(gw_correct),
        "gameWinnerAccuracyPct": round(len(gw_correct) / len(final_rows) * 100, 2) if final_rows else None,
        "b10AuditedGameCount": len(b10_known),
        "b10Correct": len(b10_correct),
        "b10Wrong": len(b10_known) - len(b10_correct),
        "b10AccuracyPct": round(len(b10_correct) / len(b10_known) * 100, 2) if b10_known else None,
    }


def store_audit(report: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    slate = report.get("slate_date")
    item = history.ddb_safe({
        "PK": f"MLB_DAILY_AUDIT#{slate}",
        "SK": f"AUDIT#{report.get('createdAt')}",
        "record_type": "mlb_yesterday_game_audit",
        "sport": "mlb",
        "slate_date": slate,
        "created_at": report.get("createdAt"),
        "data": report,
    })
    latest = history.ddb_safe({
        "PK": "MLB_DAILY_AUDIT#LATEST",
        "SK": "LATEST",
        "record_type": "mlb_yesterday_game_audit_latest",
        "sport": "mlb",
        "slate_date": slate,
        "created_at": report.get("createdAt"),
        "data": report,
    })
    history.PULLS.put_item(Item=item)
    history.PULLS.put_item(Item=latest)
    return {"ok": True, "pk": item["PK"], "sk": item["SK"]}


def build(slate_date: Optional[str] = None, days_from: int = 3, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
    slate = slate_date or yesterday_et()
    score_report = pull_final_scores(slate, days_from=days_from)
    rows = audit_rows(slate, score_report)
    summary = summarize(rows)
    report = {
        "ok": True,
        "proofType": "MLB_YESTERDAY_GAME_AUDIT",
        "createdAt": now_iso(),
        "sport": "mlb",
        "slate_date": slate,
        "finalScoreCount": score_report.get("finalScoreCount"),
        "summary": summary,
        "rows": rows,
        "finalScores": score_report.get("finalScores") or [],
        "policy": "Audit every completed MLB game from the previous ET slate. Grade the game-winner pick and the B10 selected pick against final winners.",
    }
    if store:
        try:
            report["stored"] = store_audit(report)
        except Exception as exc:
            report["storeError"] = str(exc)
    if write_file:
        os.makedirs("runtime_reports", exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
            f.write("\n")
    return report


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
