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
TARGET_ACCURACY_PCT = 90.0
HISTORICAL_AUDIT_WINDOW_DAYS = max(
    1,
    int(os.environ.get("INQSI_MLB_HISTORICAL_AUDIT_WINDOW_DAYS", "60")),
)
# A positive legacy run limit remains available as an explicit operational
# circuit breaker. The default is uncapped because 720 twice-hourly runs retained
# only about 15 days/~225 MLB games and made the 500-row promotion gate impossible.
HISTORICAL_AUDIT_RUN_LIMIT = max(
    0,
    int(os.environ.get("INQSI_MLB_HISTORICAL_AUDIT_RUN_LIMIT", "0")),
)
MIN_MULTI_WINDOW_SAMPLE = int(os.environ.get("INQSI_MLB_MIN_MULTI_WINDOW_SAMPLE", "6"))
MULTI_WINDOW_WEIGHTS = {
    "current24h": float(os.environ.get("INQSI_MLB_WEIGHT_CURRENT_24H", "0.50")),
    "sevenDay": float(os.environ.get("INQSI_MLB_WEIGHT_7D", "0.25")),
    "thirtyDay": float(os.environ.get("INQSI_MLB_WEIGHT_30D", "0.15")),
    "season": float(os.environ.get("INQSI_MLB_WEIGHT_SEASON", "0.10")),
}
MULTI_WINDOW_DAYS = {
    "current24h": 1,
    "sevenDay": 7,
    "thirtyDay": 30,
    "season": None,
}


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
        if not history.mlb_model_eligible_game(game):
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
    out: List[Dict[str, Any]] = []
    start_key = None
    while True:
        args = {"KeyConditionExpression": history.Key("PK").eq(f"GAME_WINNERS#mlb#{slate_date}")}
        if start_key:
            args["ExclusiveStartKey"] = start_key
        resp = history.PULLS.query(**args)
        for item in resp.get("Items") or []:
            data = item.get("data") or item
            if isinstance(data, dict):
                out.append(data)
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
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
            "confidenceTier": pred.get("confidenceTier"),
            "tags": pred.get("tags") or [],
            "selectionBeforeWinnerOptimizer": pred.get("selectionBeforeWinnerOptimizer"),
            "individualWinnerOptimized": pred.get("individualWinnerOptimized"),
            "optimizerFlippedPick": pred.get("optimizerFlippedPick"),
            "winnerOptimizer": pred.get("winnerOptimizer"),
            "homeSignal": pred.get("homeSignal"),
            "awaySignal": pred.get("awaySignal"),
            "correct": correct,
        })
    return rows


def _historical_key(row: Dict[str, Any]) -> str:
    return "|".join([
        str(row.get("id") or ""),
        str(row.get("gameKeyBase") or ""),
        str(row.get("commenceTime") or ""),
        str(row.get("predictedWinner") or ""),
    ])


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if row.get("status") != "GRADED":
            continue
        key = _historical_key(row)
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def historical_audit_rows(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return deduped historical graded rows from prior stored audit runs.

    The workflow runs twice hourly, so the same completed game can appear in many
    rolling 24h reports. Dedupe prevents repeat runs from overweighting one game.
    """
    if history.PULLS is None:
        return []
    seen: Dict[str, Dict[str, Any]] = {}
    start_key = None
    configured_limit = HISTORICAL_AUDIT_RUN_LIMIT if limit is None else max(0, int(limit))
    queried_runs = 0
    cutoff = now_utc() - timedelta(days=HISTORICAL_AUDIT_WINDOW_DAYS)
    window_complete = False
    while not window_complete:
        try:
            args = {
                "KeyConditionExpression": history.Key("PK").eq("MLB_ROLLING_24H_AUDIT#RUNS"),
                "ScanIndexForward": False,
                "Limit": min(100, configured_limit - queried_runs) if configured_limit else 100,
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            resp = history.PULLS.query(**args)
        except Exception:
            return list(seen.values())
        for item in resp.get("Items") or []:
            queried_runs += 1
            data = item.get("data") or {}
            audit_created = item.get("created_at") or data.get("createdAt")
            audit_created_dt = parse_dt(audit_created)
            if audit_created_dt and audit_created_dt < cutoff:
                # Runs are queried newest-first. Once a well-formed run is older
                # than the configured durable window, every later page is older.
                window_complete = True
                break
            for row in data.get("rows") or []:
                if row.get("status") != "GRADED":
                    continue
                key = _historical_key(row)
                if key not in seen:
                    copied = dict(row)
                    copied["sourceAuditCreatedAt"] = audit_created
                    seen[key] = copied
        start_key = resp.get("LastEvaluatedKey")
        if window_complete or not start_key:
            break
        if configured_limit and queried_runs >= configured_limit:
            break
    return list(seen.values())


def _rows_since(rows: List[Dict[str, Any]], days: Optional[int]) -> List[Dict[str, Any]]:
    if days is None:
        return _dedupe_rows(rows)
    cutoff = now_utc() - timedelta(days=days)
    out = []
    for row in _dedupe_rows(rows):
        dt = parse_dt(row.get("commenceTime"))
        if dt and dt >= cutoff:
            out.append(row)
    return out


def _accuracy(rows: List[Dict[str, Any]]) -> Optional[float]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    if not graded:
        return None
    return round(sum(1 for r in graded if r.get("correct")) / len(graded) * 100.0, 2)


def _tag_combo(tags: List[str]) -> str:
    return "+".join(sorted(set(tags or []))) or "NO_TAGS"


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


def _bounded_adjustment(acc: Optional[float], count: int, scale: float, cap: float) -> float:
    if acc is None:
        return 0.0
    if count <= 0:
        return 0.0
    if count == 1:
        return 0.75 if acc >= 100 else -0.75
    return round(max(-cap, min(cap, (float(acc) - 50.0) / scale)), 2)


def _adjustments_from_stats(stats: Dict[str, Dict[str, Any]], scale: float, cap: float) -> Dict[str, float]:
    return {
        key: _bounded_adjustment(stat.get("accuracyPct"), int(stat.get("count") or 0), scale=scale, cap=cap)
        for key, stat in stats.items()
    }


def _window_stat_package(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    graded = [r for r in rows if r.get("status") == "GRADED"]
    correct = [r for r in graded if r.get("correct")]
    tag_stats = _bucket(graded, lambda r: r.get("tags") or [])
    combo_stats = _bucket(graded, lambda r: _tag_combo(r.get("tags") or []))
    confidence_stats = _bucket(graded, lambda r: r.get("confidenceTier") or "UNKNOWN")
    flip_stats = _bucket(graded, lambda r: "FLIPPED" if r.get("optimizerFlippedPick") else "NOT_FLIPPED")
    return {
        "rowCount": len(graded),
        "correct": len(correct),
        "wrong": len(graded) - len(correct),
        "accuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
        "tagStats": tag_stats,
        "tagComboStats": combo_stats,
        "confidenceStats": confidence_stats,
        "optimizerFlipStats": flip_stats,
        "tagScoreAdjustments": _adjustments_from_stats(tag_stats, scale=18.0, cap=3.0),
        "tagComboScoreAdjustments": _adjustments_from_stats(combo_stats, scale=12.0, cap=5.0),
    }


def _build_learning_windows(current_rows: List[Dict[str, Any]], historical_rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    all_rows = _dedupe_rows((current_rows or []) + (historical_rows or []))
    return {
        "current24h": _window_stat_package(_rows_since(current_rows, 1)),
        "sevenDay": _window_stat_package(_rows_since(all_rows, 7)),
        "thirtyDay": _window_stat_package(_rows_since(all_rows, 30)),
        "season": _window_stat_package(_rows_since(all_rows, None)),
    }


def _blend_multi_window_adjustments(windows: Dict[str, Dict[str, Any]], adjustment_key: str, stats_key: str) -> Dict[str, float]:
    keys = sorted(set().union(*[(windows.get(w) or {}).get(adjustment_key, {}).keys() for w in MULTI_WINDOW_WEIGHTS]))
    out: Dict[str, float] = {}
    for key in keys:
        weighted_sum = 0.0
        weight_sum = 0.0
        for window_name, configured_weight in MULTI_WINDOW_WEIGHTS.items():
            package = windows.get(window_name) or {}
            adjustments = package.get(adjustment_key) or {}
            stats = (package.get(stats_key) or {}).get(key) or {}
            count = int(stats.get("count") or 0)
            if key not in adjustments:
                continue
            if window_name != "current24h" and count < MIN_MULTI_WINDOW_SAMPLE:
                continue
            weighted_sum += float(adjustments.get(key) or 0.0) * float(configured_weight)
            weight_sum += float(configured_weight)
        out[key] = round(max(-8.0, min(8.0, weighted_sum / weight_sum)), 2) if weight_sum else 0.0
    return out


def score_learning(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    historical_rows = historical_rows or []
    windows = _build_learning_windows(rows, historical_rows)
    tag_adjustments = _blend_multi_window_adjustments(windows, "tagScoreAdjustments", "tagStats")
    combo_adjustments = _blend_multi_window_adjustments(windows, "tagComboScoreAdjustments", "tagComboStats")

    return {
        "multiWindowStats": {
            name: {
                "rowCount": package.get("rowCount"),
                "correct": package.get("correct"),
                "wrong": package.get("wrong"),
                "accuracyPct": package.get("accuracyPct"),
                "tagStats": package.get("tagStats"),
                "tagComboStats": package.get("tagComboStats"),
                "confidenceStats": package.get("confidenceStats"),
                "optimizerFlipStats": package.get("optimizerFlipStats"),
            }
            for name, package in windows.items()
        },
        "windowAdjustments": {
            name: {
                "tagScoreAdjustments": package.get("tagScoreAdjustments"),
                "tagComboScoreAdjustments": package.get("tagComboScoreAdjustments"),
            }
            for name, package in windows.items()
        },
        # Backward-compatible current-window aliases for existing proof readers.
        "currentWindowStats": windows.get("current24h"),
        "historicalStats": {
            "historicalRowsUsed": len(_dedupe_rows(historical_rows)),
            "historicalWindowDays": HISTORICAL_AUDIT_WINDOW_DAYS,
            "historicalRunLimit": HISTORICAL_AUDIT_RUN_LIMIT or None,
            "historicalRunLimitMeaning": "explicit_circuit_breaker_only" if HISTORICAL_AUDIT_RUN_LIMIT else "uncapped_within_window",
            "minHistoricalSampleForBlend": MIN_MULTI_WINDOW_SAMPLE,
        },
        "tagStats": (windows.get("current24h") or {}).get("tagStats") or {},
        "tagComboStats": (windows.get("current24h") or {}).get("tagComboStats") or {},
        "confidenceStats": (windows.get("current24h") or {}).get("confidenceStats") or {},
        "optimizerFlipStats": (windows.get("current24h") or {}).get("optimizerFlipStats") or {},
        "adjustments": {
            "tagScoreAdjustments": tag_adjustments,
            "tagComboScoreAdjustments": combo_adjustments,
            "multiWindowWeights": MULTI_WINDOW_WEIGHTS,
            "minMultiWindowSampleForNonCurrentWindows": MIN_MULTI_WINDOW_SAMPLE,
        },
        "policy": "Learning blends four windows: current 24h, 7-day, 30-day, and season. Current 24h keeps the model responsive; longer windows stabilize signal scoring when sample size is sufficient.",
    }


def summarize(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    historical_rows = historical_rows or []
    graded = [r for r in rows if r.get("status") == "GRADED"]
    correct = [r for r in graded if r.get("correct")]
    optimized = [r for r in graded if r.get("individualWinnerOptimized")]
    flipped = [r for r in graded if r.get("optimizerFlippedPick")]
    all_rows = _dedupe_rows((rows or []) + (historical_rows or []))
    seven_day_rows = _rows_since(all_rows, 7)
    thirty_day_rows = _rows_since(all_rows, 30)
    season_rows = _rows_since(all_rows, None)
    return {
        "windowHours": WINDOW_HOURS,
        "targetAccuracyPct": TARGET_ACCURACY_PCT,
        "completedFinalGames": len(rows),
        "gradedPredictionCount": len(graded),
        "missingPredictionCount": len(rows) - len(graded),
        "optimizedPickCount": len(graded),
        "optimizedCorrect": len(correct),
        "optimizedWrong": len(graded) - len(correct),
        "rolling24hOptimizedAccuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
        "rolling24hTargetMet": (len(correct) / len(graded) * 100.0 >= TARGET_ACCURACY_PCT) if graded else None,
        "winnerOptimizerAppliedCount": len(optimized),
        "winnerOptimizerFlipCount": len(flipped),
        "allScoredPickAccuracyPct": _accuracy(graded),
        "historicalRowsUsedForLearning": len(_dedupe_rows(historical_rows)),
        "sevenDayRowsUsedForLearning": len(seven_day_rows),
        "sevenDayAccuracyPct": _accuracy(seven_day_rows),
        "thirtyDayRowsUsedForLearning": len(thirty_day_rows),
        "thirtyDayAccuracyPct": _accuracy(thirty_day_rows),
        "seasonRowsUsedForLearning": len(season_rows),
        "seasonAccuracyPct": _accuracy(season_rows),
        "multiWindowWeights": MULTI_WINDOW_WEIGHTS,
        "actionablePickCount": len(graded),
        "actionableCorrect": len(correct),
        "actionableWrong": len(graded) - len(correct),
        "rolling24hActionableAccuracyPct": round(len(correct) / len(graded) * 100.0, 2) if graded else None,
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
    historical_rows = historical_audit_rows()
    learning = score_learning(rows, historical_rows=historical_rows)
    report = {
        "ok": True,
        "proofType": "MLB_ROLLING_24H_AUDIT",
        "createdAt": now_iso(),
        "sport": "mlb",
        "windowHours": WINDOW_HOURS,
        "summary": summarize(rows, historical_rows=historical_rows),
        "scoreLearning": learning,
        "rows": rows,
        "policy": "Audit every completed MLB game in the trailing 24 hours. The optimizer target is correct team-winner selection for every individual game; 90% is measured as rolling 24h accuracy across all optimized picks. Signal scoring blends current 24h, 7-day, 30-day, and season audit trends.",
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
