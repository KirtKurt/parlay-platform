from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
REPORT_PATH = "runtime_reports/mlb_rolling_24h_audit_latest.json"
WINDOW_HOURS = 24
TARGET_ACCURACY_PCT = 75.0


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


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


def final_scores_last_24h(days_from: int = 3) -> List[Dict[str, Any]]:
    raw = http_get_json(scores_url(days_from=days_from))
    cutoff = now_utc() - timedelta(hours=WINDOW_HOURS)
    finals = []
    for game in raw or []:
        if not game.get("completed"):
            continue
        commence_dt = parse_dt(game.get("commence_time"))
        if not commence_dt or commence_dt < cutoff or commence_dt > now_utc() + timedelta(hours=1):
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
        finals.append({
            "id": game.get("id"),
            "gameKeyBase": f"{normalize_team(away)}|{normalize_team(home)}",
            "homeTeam": home,
            "awayTeam": away,
            "matchup": f"{away} at {home}",
            "commenceTime": game.get("commence_time"),
            "slateDateEt": slate_date_from_commence(game.get("commence_time")),
            "homeScore": home_score,
            "awayScore": away_score,
            "winner": winner,
            "margin": abs(home_score - away_score),
            "totalRuns": home_score + away_score,
            "completed": True,
        })
    return finals


def _query_predictions_for_slate(slate_date: str) -> List[Dict[str, Any]]:
    if history.PULLS is None:
        return []
    resp = history.PULLS.query(KeyConditionExpression=history.Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}"))
    out = []
    for item in resp.get("Items") or []:
        data = item.get("data") or item
        if isinstance(data, dict):
            out.append(data)
    return out


def predictions_index(finals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    dates = sorted(set([f.get("slateDateEt") for f in finals if f.get("slateDateEt")]))
    index: Dict[str, Dict[str, Any]] = {}
    for slate in dates:
        for pred in _query_predictions_for_slate(slate):
            key = f"{normalize_team(pred.get('awayTeam'))}|{normalize_team(pred.get('homeTeam'))}"
            current = index.get(key)
            if current is None or str(pred.get("createdAt") or "") > str(current.get("createdAt") or ""):
                index[key] = pred
    return index


def audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index = predictions_index(finals)
    rows = []
    for final in finals:
        pred = index.get(final.get("gameKeyBase")) or {}
        if not pred:
            rows.append({**final, "status": "MISSING_PREDICTION"})
            continue
        correct = normalize_team(pred.get("predictedWinner")) == normalize_team(final.get("winner"))
        rows.append({
            **final,
            "status": "GRADED",
            "predictedWinner": pred.get("predictedWinner"),
            "predictedSide": pred.get("predictedSide"),
            "score": pred.get("score"),
            "winProbabilityPct": pred.get("winProbabilityPct"),
            "rawScoreBeforeLearning": pred.get("rawScoreBeforeLearning"),
            "rolling24hLearningAdjustment": pred.get("rolling24hLearningAdjustment"),
            "calibrationPenalty": pred.get("calibrationPenalty"),
            "confidenceTier": pred.get("confidenceTier"),
            "tags": pred.get("tags") or [],
            "officialPick": bool(pred.get("officialPick")),
            "actionability": pred.get("actionability"),
            "actionabilityReason": pred.get("actionabilityReason"),
            "correct": correct,
        })
    return rows


def _accuracy(rows: List[Dict[str, Any]]) -> Optional[float]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    if not graded:
        return None
    return round(sum(1 for r in graded if r.get("correct")) / len(graded) * 100.0, 2)


def _bucket(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("status") != "GRADED":
            continue
        keys = key_fn(row)
        if not isinstance(keys, list):
            keys = [keys]
        for key in keys:
            if key is None:
                continue
            data = out.setdefault(str(key), {"count": 0, "correct": 0, "accuracyPct": None})
            data["count"] += 1
            if row.get("correct"):
                data["correct"] += 1
    for data in out.values():
        data["accuracyPct"] = round(data["correct"] / data["count"] * 100.0, 2) if data["count"] else None
    return out


def score_learning(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tag_stats = _bucket(rows, lambda r: r.get("tags") or [])
    action_stats = _bucket(rows, lambda r: r.get("actionability") or "UNKNOWN")
    confidence_stats = _bucket(rows, lambda r: r.get("confidenceTier") or "UNKNOWN")
    tag_adjustments: Dict[str, float] = {}
    for tag, stat in tag_stats.items():
        count = int(stat.get("count") or 0)
        acc = stat.get("accuracyPct")
        if acc is None:
            continue
        # Small rolling nudge only. A 24h window should guide, not dominate.
        if count >= 2:
            tag_adjustments[tag] = round(max(-3.0, min(3.0, (acc - 50.0) / 25.0)), 2)
        else:
            tag_adjustments[tag] = 0.5 if acc >= 100 else -0.5
    return {
        "tagStats": tag_stats,
        "actionabilityStats": action_stats,
        "confidenceStats": confidence_stats,
        "adjustments": {
            "tagScoreAdjustments": tag_adjustments,
        },
        "policy": "Rolling 24h learning nudges future signal scores from recently completed games. Adjustments are bounded to avoid overfitting one day.",
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    actionable = [r for r in graded if r.get("officialPick")]
    actionable_correct = [r for r in actionable if r.get("correct")]
    return {
        "windowHours": WINDOW_HOURS,
        "targetAccuracyPct": TARGET_ACCURACY_PCT,
        "completedFinalGames": len(rows),
        "gradedPredictionCount": len(graded),
        "missingPredictionCount": len(rows) - len(graded),
        "allScoredPickAccuracyPct": _accuracy(graded),
        "actionablePickCount": len(actionable),
        "actionableCorrect": len(actionable_correct),
        "actionableWrong": len(actionable) - len(actionable_correct),
        "rolling24hActionableAccuracyPct": round(len(actionable_correct) / len(actionable) * 100.0, 2) if actionable else None,
        "rolling24hTargetMet": (len(actionable_correct) / len(actionable) * 100.0 >= TARGET_ACCURACY_PCT) if actionable else None,
    }


def store_report(report: Dict[str, Any]) -> Dict[str, Any]:
    if history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    item = history.ddb_safe({
        "PK": "MLB_ROLLING_24H_AUDIT#LATEST",
        "SK": "LATEST",
        "record_type": "mlb_rolling_24h_audit_latest",
        "sport": "mlb",
        "created_at": report.get("createdAt"),
        "data": report,
    })
    dated = history.ddb_safe({
        "PK": "MLB_ROLLING_24H_AUDIT#RUNS",
        "SK": f"AUDIT#{report.get('createdAt')}",
        "record_type": "mlb_rolling_24h_audit_run",
        "sport": "mlb",
        "created_at": report.get("createdAt"),
        "data": report,
    })
    history.PULLS.put_item(Item=item)
    history.PULLS.put_item(Item=dated)
    return {"ok": True, "latestPk": item["PK"], "latestSk": item["SK"], "runPk": dated["PK"], "runSk": dated["SK"]}


def build(days_from: int = 3, store: bool = True, write_file: bool = True) -> Dict[str, Any]:
    finals = final_scores_last_24h(days_from=days_from)
    rows = audit_rows(finals)
    learning = score_learning(rows)
    report = {
        "ok": True,
        "proofType": "MLB_ROLLING_24H_AUDIT",
        "createdAt": now_iso(),
        "sport": "mlb",
        "windowHours": WINDOW_HOURS,
        "summary": summarize(rows),
        "scoreLearning": learning,
        "rows": rows,
        "policy": "Audit every completed MLB game in the trailing 24 hours. The 75% target is measured only as the rolling 24h accuracy of actionable picks, not as a per-pick guarantee.",
    }
    if store:
        try:
            report["stored"] = store_report(report)
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
