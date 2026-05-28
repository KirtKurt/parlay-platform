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
    total_runs = None
    if home_score is not None and away_score is not None:
        margin = abs(home_score - away_score)
        total_runs = home_score + away_score
        if home_score > away_score:
            winner = home
        elif away_score > home_score:
            winner = away
        else:
            winner = "TIE"

    return {
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
        "margin": margin,
        "total_runs": total_runs,
    }


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
            "total_runs": parsed["total_runs"],
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


def _team_score(outcome: Dict[str, Any], team: str) -> Optional[int]:
    if team == outcome.get("home_team"):
        return outcome.get("home_score")
    if team == outcome.get("away_team"):
        return outcome.get("away_score")
    return None


def _opponent_score(outcome: Dict[str, Any], team: str) -> Optional[int]:
    if team == outcome.get("home_team"):
        return outcome.get("away_score")
    if team == outcome.get("away_team"):
        return outcome.get("home_score")
    return None


def _evaluate_moneyline(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_team = pred.get("predicted_team")
    success = predicted_team == outcome.get("winner")
    return {
        "success": success,
        "evaluated_rule": "predicted_team_won_moneyline",
        "winner": outcome.get("winner"),
    }


def _evaluate_spread(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_team = pred.get("predicted_team")
    spread_point = pred.get("spread_point") or pred.get("line") or pred.get("prediction_point")
    if spread_point is None:
        return {"success": None, "evaluated_rule": "spread_missing_line"}

    team_score = _team_score(outcome, predicted_team)
    opp_score = _opponent_score(outcome, predicted_team)
    if team_score is None or opp_score is None:
        return {"success": None, "evaluated_rule": "spread_team_not_found"}

    adjusted_margin = Decimal(str(team_score)) + Decimal(str(spread_point)) - Decimal(str(opp_score))
    if adjusted_margin > 0:
        success = True
        result = "COVER"
    elif adjusted_margin < 0:
        success = False
        result = "NO_COVER"
    else:
        success = None
        result = "PUSH"

    return {
        "success": success,
        "evaluated_rule": "predicted_team_covered_spread",
        "spread_point": Decimal(str(spread_point)),
        "adjusted_margin": adjusted_margin,
        "spread_result": result,
    }


def _evaluate_total(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_total_side = (pred.get("predicted_total_side") or pred.get("predicted_side") or "").lower()
    total_point = pred.get("total_point") or pred.get("line") or pred.get("prediction_point")
    total_runs = outcome.get("total_runs")
    if total_point is None or total_runs is None:
        return {"success": None, "evaluated_rule": "total_missing_line_or_score"}

    total_runs_dec = Decimal(str(total_runs))
    total_point_dec = Decimal(str(total_point))
    if total_runs_dec > total_point_dec:
        actual = "over"
    elif total_runs_dec < total_point_dec:
        actual = "under"
    else:
        actual = "push"

    if actual == "push":
        success = None
    else:
        success = predicted_total_side == actual

    return {
        "success": success,
        "evaluated_rule": "predicted_total_side_hit",
        "predicted_total_side": predicted_total_side,
        "actual_total_side": actual,
        "total_point": total_point_dec,
        "total_runs": total_runs_dec,
    }


def _evaluate_prediction_against_outcome(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    market = (pred.get("market") or pred.get("prediction_market") or "moneyline").lower()
    if market in {"moneyline", "ml", "h2h"}:
        result = _evaluate_moneyline(pred, outcome)
    elif market in {"spread", "spreads"}:
        result = _evaluate_spread(pred, outcome)
    elif market in {"total", "totals", "over_under", "ou"}:
        result = _evaluate_total(pred, outcome)
    else:
        result = {"success": None, "evaluated_rule": "unknown_market"}

    result.update({
        "market": market,
        "winner": outcome.get("winner"),
        "home_score": outcome.get("home_score"),
        "away_score": outcome.get("away_score"),
        "margin": outcome.get("margin"),
        "total_runs": outcome.get("total_runs"),
        "prediction_type": pred.get("prediction_type"),
    })
    return result


def evaluate_mlb_predictions(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")

    slate_date = slate_date or _slate_date_et()
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
    items = resp.get("Items", [])
    evaluated = []
    skipped_push_or_ungradable = []
    open_missing_outcome = []

    for pred in items:
        if pred.get("status") in {"CORRECT", "WRONG", "PUSH", "UNGRADABLE"}:
            evaluated.append(pred)
            continue

        outcome = _outcome_for_game(slate_date, pred.get("game_key"))
        if not outcome:
            open_missing_outcome.append(pred.get("SK"))
            continue

        evaluation = _evaluate_prediction_against_outcome(pred, outcome)
        success = evaluation.get("success")
        if success is True:
            status = "CORRECT"
        elif success is False:
            status = "WRONG"
        elif evaluation.get("spread_result") == "PUSH" or evaluation.get("actual_total_side") == "push":
            status = "PUSH"
            skipped_push_or_ungradable.append(pred.get("SK"))
        else:
            status = "UNGRADABLE"
            skipped_push_or_ungradable.append(pred.get("SK"))

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

    by_market: Dict[str, Dict[str, int]] = {}
    for pred in evaluated:
        market = (pred.get("market") or pred.get("prediction_market") or "moneyline").lower()
        by_market.setdefault(market, {"correct": 0, "wrong": 0, "graded": 0})
        if pred.get("status") == "CORRECT":
            by_market[market]["correct"] += 1
            by_market[market]["graded"] += 1
        elif pred.get("status") == "WRONG":
            by_market[market]["wrong"] += 1
            by_market[market]["graded"] += 1

    for market, data in by_market.items():
        data["accuracy_pct"] = round(data["correct"] / data["graded"] * 100, 2) if data["graded"] else None

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
        "by_market": by_market,
        "missing_outcome_count": len(open_missing_outcome),
        "skipped_push_or_ungradable_count": len(skipped_push_or_ungradable),
    }


def mlb_training_export(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
    rows: List[Dict[str, Any]] = []
    for pred in resp.get("Items", []):
        evaluation = pred.get("evaluation", {})
        rows.append({
            "sport": pred.get("sport"),
            "slate_date_et": pred.get("slate_date_et"),
            "game_key": pred.get("game_key"),
            "home_team": pred.get("home_team"),
            "away_team": pred.get("away_team"),
            "market": pred.get("market") or pred.get("prediction_market") or "moneyline",
            "prediction_type": pred.get("prediction_type"),
            "predicted_team": pred.get("predicted_team"),
            "predicted_side": pred.get("predicted_side"),
            "predicted_total_side": pred.get("predicted_total_side"),
            "spread_point": float(pred.get("spread_point", 0)) if pred.get("spread_point") is not None else None,
            "total_point": float(pred.get("total_point", 0)) if pred.get("total_point") is not None else None,
            "home_delta": float(pred.get("home_delta", 0)),
            "away_delta": float(pred.get("away_delta", 0)),
            "spread_delta": float(pred.get("spread_delta", 0)) if pred.get("spread_delta") is not None else None,
            "total_delta": float(pred.get("total_delta", 0)) if pred.get("total_delta") is not None else None,
            "confidence": float(pred.get("confidence", 0)),
            "status": pred.get("status"),
            "success": pred.get("success"),
            "winner": evaluation.get("winner"),
            "home_score": evaluation.get("home_score"),
            "away_score": evaluation.get("away_score"),
            "total_runs": evaluation.get("total_runs"),
            "actual_total_side": evaluation.get("actual_total_side"),
            "spread_result": evaluation.get("spread_result"),
            "evaluated_rule": evaluation.get("evaluated_rule"),
        })
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "row_count": len(rows), "rows": rows}
