import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")

predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _scores_url(sport: str = "baseball_mlb", days_from: int = 3) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {"apiKey": ODDS_API_KEY, "daysFrom": days_from, "dateFormat": "iso"}
    return f"https://api.the-odds-api.com/v4/sports/{sport}/scores/?" + urllib.parse.urlencode(params)


def _game_key_from_score(score: Dict[str, Any]) -> str:
    home = score.get("home_team")
    away = score.get("away_team")
    commence = score.get("commence_time") or ""
    slate_date = _slate_date_et()
    if commence:
        try:
            dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            slate_date = dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            pass
    return f"mlb|{slate_date}|{_normalize_team(away)}|{_normalize_team(home)}"


def _extract_scores(score: Dict[str, Any]) -> Dict[str, Any]:
    home = score.get("home_team")
    away = score.get("away_team")
    home_score = None
    away_score = None
    for row in score.get("scores") or []:
        if row.get("name") == home:
            home_score = int(row.get("score"))
        elif row.get("name") == away:
            away_score = int(row.get("score"))
    winner = None
    margin = None
    if home_score is not None and away_score is not None:
        margin = abs(home_score - away_score)
        if home_score > away_score:
            winner = home
        elif away_score > home_score:
            winner = away
        else:
            winner = "TIE"
    return {"home_score": home_score, "away_score": away_score, "winner": winner, "margin": margin}


def pull_mlb_results(days_from: int = 3) -> Dict[str, Any]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    raw_scores = _http_get_json(_scores_url(days_from=days_from))
    stored = []
    now = _now_iso()
    for score in raw_scores or []:
        if not score.get("completed"):
            continue
        parsed = _extract_scores(score)
        if parsed.get("winner") is None:
            continue
        commence = score.get("commence_time") or now
        try:
            slate_date = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            slate_date = _slate_date_et()
        game_key = _game_key_from_score(score)
        item = {
            "PK": f"OUTCOME#mlb#{slate_date}",
            "SK": f"GAME#{game_key}",
            "sport": "mlb",
            "slate_date_et": slate_date,
            "game_key": game_key,
            "game_id": score.get("id"),
            "home_team": score.get("home_team"),
            "away_team": score.get("away_team"),
            "commence_time": score.get("commence_time"),
            "completed": True,
            "home_score": parsed["home_score"],
            "away_score": parsed["away_score"],
            "winner": parsed["winner"],
            "margin": parsed["margin"],
            "source": "theOddsAPI_scores",
            "updated_at": now,
        }
        outcomes_tbl.put_item(Item=item)
        stored.append(item)
    return {"ok": True, "sport": "mlb", "stored_count": len(stored), "results": stored}


def _outcome_for_game(slate_date: str, game_key: str) -> Optional[Dict[str, Any]]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    resp = outcomes_tbl.get_item(Key={"PK": f"OUTCOME#mlb#{slate_date}", "SK": f"GAME#{game_key}"})
    return resp.get("Item")


def evaluate_mlb_predictions(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
    items = resp.get("Items", [])
    evaluated = []
    open_missing_outcome = []
    for pred in items:
        if pred.get("status") in {"CORRECT", "WRONG"}:
            evaluated.append(pred)
            continue
        outcome = _outcome_for_game(slate_date, pred.get("game_key"))
        if not outcome:
            open_missing_outcome.append(pred.get("SK"))
            continue
        predicted_team = pred.get("predicted_team")
        success = predicted_team == outcome.get("winner")
        status = "CORRECT" if success else "WRONG"
        evaluation = {
            "winner": outcome.get("winner"),
            "home_score": outcome.get("home_score"),
            "away_score": outcome.get("away_score"),
            "margin": outcome.get("margin"),
            "prediction_type": pred.get("prediction_type"),
            "evaluated_rule": "predicted_hot_team_won_game",
        }
        predictions_tbl.update_item(
            Key={"PK": pred["PK"], "SK": pred["SK"]},
            UpdateExpression="SET #s=:s, evaluated_at=:e, success=:x, evaluation=:v",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":e": _now_iso(), ":x": success, ":v": evaluation},
        )
        pred["status"] = status
        pred["success"] = success
        pred["evaluation"] = evaluation
        evaluated.append(pred)
    correct = [p for p in evaluated if p.get("status") == "CORRECT"]
    wrong = [p for p in evaluated if p.get("status") == "WRONG"]
    total_eval = len(correct) + len(wrong)
    accuracy = round(len(correct) / total_eval * 100, 2) if total_eval else None
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "target_success_rate": 75,
        "evaluated": total_eval,
        "correct": len(correct),
        "wrong": len(wrong),
        "accuracy_pct": accuracy,
        "meets_target": accuracy is not None and accuracy >= 75,
        "missing_outcome_count": len(open_missing_outcome),
    }


def mlb_training_export(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
    rows: List[Dict[str, Any]] = []
    for pred in resp.get("Items", []):
        rows.append({
            "sport": pred.get("sport"),
            "slate_date_et": pred.get("slate_date_et"),
            "game_key": pred.get("game_key"),
            "home_team": pred.get("home_team"),
            "away_team": pred.get("away_team"),
            "prediction_type": pred.get("prediction_type"),
            "predicted_team": pred.get("predicted_team"),
            "predicted_side": pred.get("predicted_side"),
            "home_delta": float(pred.get("home_delta", 0)),
            "away_delta": float(pred.get("away_delta", 0)),
            "confidence": float(pred.get("confidence", 0)),
            "status": pred.get("status"),
            "success": pred.get("success"),
            "evaluation": pred.get("evaluation", {}),
        })
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "row_count": len(rows), "rows": rows}
