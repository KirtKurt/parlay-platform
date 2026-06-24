import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")

predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None

FINAL_SCORE_SOURCE = "theOddsAPI_scores"
SETTLEMENT_VERSION = "MLB-B1.0-postgame-settlement-v1"
PARLAY_TYPES = {"MLB_THREE_LEG_PARLAY_ATTEMPT", "MLB_PARLAY_ATTEMPT", "THREE_LEG_MLB_PARLAY"}
FINAL_PREDICTION_STATUSES = {"CORRECT", "WRONG", "PUSH", "UNGRADABLE"}
FINAL_PARLAY_STATUSES = {"WON", "LOST", "PUSH", "UNGRADABLE"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_ddb_safe(v) for v in value if v is not None]
    return value


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _scores_url(sport: str = "baseball_mlb", days_from: int = 3) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {"apiKey": ODDS_API_KEY, "daysFrom": days_from, "dateFormat": "iso"}
    return f"https://api.the-odds-api.com/v4/sports/{sport}/scores/?" + urllib.parse.urlencode(params)


def _slate_date_from_commence(commence_time: Optional[str]) -> str:
    if commence_time:
        try:
            dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
            return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            pass
    return _slate_date_et()


def _game_key_from_score(score: Dict[str, Any]) -> str:
    home = score.get("home_team")
    away = score.get("away_team")
    slate_date = _slate_date_from_commence(score.get("commence_time"))
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
    home_margin = None
    away_margin = None
    if home_score is not None and away_score is not None:
        home_margin = home_score - away_score
        away_margin = away_score - home_score
        margin = abs(home_margin)
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
        "home_margin": home_margin,
        "away_margin": away_margin,
        "total_runs": total_runs,
    }


def pull_mlb_results(days_from: int = 3) -> Dict[str, Any]:
    """Fetch completed MLB games only and store final scores in OutcomesTable."""
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")

    raw_scores = _http_get_json(_scores_url(days_from=days_from))
    stored = []
    skipped_live_or_unstarted = 0
    skipped_missing_score = 0
    now = _now_iso()

    for score in raw_scores or []:
        if not score.get("completed"):
            skipped_live_or_unstarted += 1
            continue
        parsed = _extract_scores(score)
        if parsed.get("winner") is None:
            skipped_missing_score += 1
            continue

        commence = score.get("commence_time") or now
        slate_date = _slate_date_from_commence(commence)
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
            "home_margin": parsed["home_margin"],
            "away_margin": parsed["away_margin"],
            "total_runs": parsed["total_runs"],
            "source": FINAL_SCORE_SOURCE,
            "settlement_version": SETTLEMENT_VERSION,
            "updated_at": now,
        }
        outcomes_tbl.put_item(Item=_ddb_safe(item))
        stored.append(item)

    return {
        "ok": True,
        "sport": "mlb",
        "source": FINAL_SCORE_SOURCE,
        "final_only": True,
        "stored_count": len(stored),
        "skipped_live_or_unstarted": skipped_live_or_unstarted,
        "skipped_missing_score": skipped_missing_score,
        "results": _jsonable(stored),
    }


def _outcome_for_game(slate_date: str, game_key: str) -> Optional[Dict[str, Any]]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    resp = outcomes_tbl.get_item(Key={"PK": f"OUTCOME#mlb#{slate_date}", "SK": f"GAME#{game_key}"})
    return resp.get("Item")


def _outcomes_for_slate(slate_date: str) -> List[Dict[str, Any]]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    resp = outcomes_tbl.query(KeyConditionExpression=Key("PK").eq(f"OUTCOME#mlb#{slate_date}"))
    return resp.get("Items", [])


def _predictions_for_slate(slate_date: str) -> List[Dict[str, Any]]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
    return resp.get("Items", [])


def _team_score(outcome: Dict[str, Any], team: str) -> Optional[int]:
    if _normalize_team(team) == _normalize_team(outcome.get("home_team")):
        return outcome.get("home_score")
    if _normalize_team(team) == _normalize_team(outcome.get("away_team")):
        return outcome.get("away_score")
    return None


def _opponent_score(outcome: Dict[str, Any], team: str) -> Optional[int]:
    if _normalize_team(team) == _normalize_team(outcome.get("home_team")):
        return outcome.get("away_score")
    if _normalize_team(team) == _normalize_team(outcome.get("away_team")):
        return outcome.get("home_score")
    return None


def _evaluate_moneyline(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_team = pred.get("predicted_team") or pred.get("attempted_winner")
    success = _normalize_team(predicted_team) == _normalize_team(outcome.get("winner"))
    return {
        "success": success,
        "leg_result": "WON" if success else "LOST",
        "evaluated_rule": "predicted_team_won_moneyline",
        "predicted_team": predicted_team,
        "winner": outcome.get("winner"),
    }


def _line_from_prediction(pred: Dict[str, Any]) -> Optional[Decimal]:
    for key in ("run_line", "runLine", "runline", "spread_point", "spreadPoint", "line", "prediction_point"):
        point = _decimal(pred.get(key))
        if point is not None:
            return point
    return None


def _evaluate_runline(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_team = pred.get("predicted_team") or pred.get("attempted_winner")
    point = _line_from_prediction(pred)
    if point is None:
        return {"success": None, "leg_result": "UNGRADABLE", "evaluated_rule": "run_line_missing_line", "predicted_team": predicted_team}

    team_score = _team_score(outcome, predicted_team)
    opp_score = _opponent_score(outcome, predicted_team)
    if team_score is None or opp_score is None:
        return {"success": None, "leg_result": "UNGRADABLE", "evaluated_rule": "run_line_team_not_found", "predicted_team": predicted_team, "run_line": point}

    adjusted_margin = Decimal(str(team_score)) + point - Decimal(str(opp_score))
    if adjusted_margin > 0:
        success = True
        result = "COVER"
        leg_result = "WON"
    elif adjusted_margin < 0:
        success = False
        result = "NO_COVER"
        leg_result = "LOST"
    else:
        success = None
        result = "PUSH"
        leg_result = "PUSH"

    return {
        "success": success,
        "leg_result": leg_result,
        "evaluated_rule": "predicted_team_covered_run_line",
        "predicted_team": predicted_team,
        "run_line": point,
        "team_score": team_score,
        "opponent_score": opp_score,
        "adjusted_margin": adjusted_margin,
        "spread_result": result,
    }


def _evaluate_total(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    predicted_total_side = (pred.get("predicted_total_side") or pred.get("predicted_side") or "").lower()
    total_point = _decimal(pred.get("total_point") or pred.get("line") or pred.get("prediction_point"))
    total_runs = outcome.get("total_runs")
    if total_point is None or total_runs is None:
        return {"success": None, "leg_result": "UNGRADABLE", "evaluated_rule": "total_missing_line_or_score"}

    total_runs_dec = Decimal(str(total_runs))
    if total_runs_dec > total_point:
        actual = "over"
    elif total_runs_dec < total_point:
        actual = "under"
    else:
        actual = "push"

    if actual == "push":
        success = None
        leg_result = "PUSH"
    else:
        success = predicted_total_side == actual
        leg_result = "WON" if success else "LOST"

    return {
        "success": success,
        "leg_result": leg_result,
        "evaluated_rule": "predicted_total_side_hit",
        "predicted_total_side": predicted_total_side,
        "actual_total_side": actual,
        "total_point": total_point,
        "total_runs": total_runs_dec,
    }


def _market_name(pred: Dict[str, Any]) -> str:
    return (pred.get("market") or pred.get("prediction_market") or "moneyline").lower()


def _evaluate_prediction_against_outcome(pred: Dict[str, Any], outcome: Dict[str, Any]) -> Dict[str, Any]:
    market = _market_name(pred)
    if market in {"moneyline", "ml", "h2h"}:
        result = _evaluate_moneyline(pred, outcome)
    elif market in {"runline", "run_line", "run line", "spread", "spreads"}:
        result = _evaluate_runline(pred, outcome)
    elif market in {"total", "totals", "over_under", "ou"}:
        result = _evaluate_total(pred, outcome)
    else:
        result = {"success": None, "leg_result": "UNGRADABLE", "evaluated_rule": "unknown_market"}

    result.update({
        "market": market,
        "winner": outcome.get("winner"),
        "home_score": outcome.get("home_score"),
        "away_score": outcome.get("away_score"),
        "margin": outcome.get("margin"),
        "total_runs": outcome.get("total_runs"),
        "prediction_type": pred.get("prediction_type"),
        "game_key": pred.get("game_key"),
        "completed": bool(outcome.get("completed")),
        "final_only": True,
    })
    return result


def _prediction_status_from_evaluation(evaluation: Dict[str, Any]) -> str:
    success = evaluation.get("success")
    if success is True:
        return "CORRECT"
    if success is False:
        return "WRONG"
    if evaluation.get("leg_result") == "PUSH" or evaluation.get("spread_result") == "PUSH" or evaluation.get("actual_total_side") == "push":
        return "PUSH"
    return "UNGRADABLE"


def _is_parlay_item(item: Dict[str, Any]) -> bool:
    prediction_type = item.get("prediction_type") or item.get("card_type")
    return prediction_type in PARLAY_TYPES or str(item.get("SK") or "").startswith("PARLAY#")


def _is_individual_prediction(item: Dict[str, Any]) -> bool:
    return not _is_parlay_item(item)


def evaluate_mlb_predictions(slate_date: Optional[str] = None) -> Dict[str, Any]:
    """Grade stored individual MLB picks against stored final scores. No live games are graded."""
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")

    slate_date = slate_date or _slate_date_et()
    items = _predictions_for_slate(slate_date)
    evaluated = []
    skipped_push_or_ungradable = []
    open_missing_outcome = []

    for pred in items:
        if not _is_individual_prediction(pred):
            continue
        if pred.get("status") in FINAL_PREDICTION_STATUSES:
            evaluated.append(pred)
            continue

        outcome = _outcome_for_game(slate_date, pred.get("game_key"))
        if not outcome or not outcome.get("completed"):
            open_missing_outcome.append(pred.get("SK"))
            continue

        evaluation = _evaluate_prediction_against_outcome(pred, outcome)
        status = _prediction_status_from_evaluation(evaluation)
        if status in {"PUSH", "UNGRADABLE"}:
            skipped_push_or_ungradable.append(pred.get("SK"))

        predictions_tbl.update_item(
            Key={"PK": pred["PK"], "SK": pred["SK"]},
            UpdateExpression="SET #s=:s, evaluated_at=:e, success=:x, evaluation=:v, settlement_version=:sv",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":e": _now_iso(), ":x": evaluation.get("success"), ":v": _ddb_safe(evaluation), ":sv": SETTLEMENT_VERSION},
        )
        pred["status"] = status
        pred["success"] = evaluation.get("success")
        pred["evaluation"] = _jsonable(evaluation)
        pred["settlement_version"] = SETTLEMENT_VERSION
        evaluated.append(pred)

    correct = [p for p in evaluated if p.get("status") == "CORRECT"]
    wrong = [p for p in evaluated if p.get("status") == "WRONG"]
    total_eval = len(correct) + len(wrong)
    accuracy = round(len(correct) / total_eval * 100, 2) if total_eval else None

    by_market: Dict[str, Dict[str, int]] = {}
    for pred in evaluated:
        market = _market_name(pred)
        by_market.setdefault(market, {"correct": 0, "wrong": 0, "graded": 0, "push": 0, "ungradable": 0})
        if pred.get("status") == "CORRECT":
            by_market[market]["correct"] += 1
            by_market[market]["graded"] += 1
        elif pred.get("status") == "WRONG":
            by_market[market]["wrong"] += 1
            by_market[market]["graded"] += 1
        elif pred.get("status") == "PUSH":
            by_market[market]["push"] += 1
        elif pred.get("status") == "UNGRADABLE":
            by_market[market]["ungradable"] += 1

    for data in by_market.values():
        data["accuracy_pct"] = round(data["correct"] / data["graded"] * 100, 2) if data["graded"] else None

    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "settlement_version": SETTLEMENT_VERSION,
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


def _leg_from_combo(leg: Dict[str, Any], picked_team: str) -> Dict[str, Any]:
    return {
        "game_key": leg.get("game_key"),
        "match": leg.get("match"),
        "market": "moneyline",
        "predicted_team": picked_team,
        "predicted_side": "home" if _normalize_team(picked_team) == _normalize_team((leg.get("home") or {}).get("team")) else "away",
    }


def _grade_leg(leg: Dict[str, Any], slate_date: str) -> Dict[str, Any]:
    game_key = leg.get("game_key")
    outcome = _outcome_for_game(slate_date, game_key) if game_key else None
    if not outcome or not outcome.get("completed"):
        return {
            "game_key": game_key,
            "predicted_team": leg.get("predicted_team") or leg.get("attempted_winner"),
            "market": _market_name(leg),
            "status": "PENDING",
            "reason": "NO_FINAL_OUTCOME_YET",
        }
    evaluation = _evaluate_prediction_against_outcome(leg, outcome)
    return {
        "game_key": game_key,
        "predicted_team": leg.get("predicted_team") or leg.get("attempted_winner"),
        "market": evaluation.get("market"),
        "status": evaluation.get("leg_result") or ("WON" if evaluation.get("success") is True else "LOST" if evaluation.get("success") is False else "UNGRADABLE"),
        "evaluation": _jsonable(evaluation),
    }


def _status_from_leg_results(legs: List[Dict[str, Any]]) -> str:
    statuses = {leg.get("status") for leg in legs}
    if "LOST" in statuses:
        return "LOST"
    if "PENDING" in statuses:
        return "PENDING"
    if "UNGRADABLE" in statuses:
        return "UNGRADABLE"
    if "PUSH" in statuses:
        return "PUSH"
    if legs and statuses == {"WON"}:
        return "WON"
    return "PENDING"


def _grade_parlay_item(parlay: Dict[str, Any], slate_date: str) -> Dict[str, Any]:
    base_legs = parlay.get("legs") or []
    graded_legs = []
    for leg in base_legs:
        leg_pred = {
            **leg,
            "market": leg.get("market") or "moneyline",
            "predicted_team": leg.get("predicted_team") or leg.get("attempted_winner"),
            "spread_point": leg.get("spread_point") or leg.get("run_line") or leg.get("line"),
        }
        graded_legs.append(_grade_leg(leg_pred, slate_date))

    ranked_combo_results = []
    for combo in parlay.get("ranked_combos") or []:
        combo_legs = [_grade_leg(_leg_from_combo(leg, picked_team), slate_date) for leg, picked_team in zip(base_legs, combo.get("picks") or [])]
        ranked_combo_results.append({
            "rank": combo.get("rank"),
            "picks": combo.get("picks") or [],
            "status": _status_from_leg_results(combo_legs),
            "legs": combo_legs,
            "parlay_decimal": combo.get("parlay_decimal"),
            "parlay_american": combo.get("parlay_american"),
        })

    status = _status_from_leg_results(graded_legs) if graded_legs else "PENDING"
    if ranked_combo_results:
        # The rank-1 combo is the product result shown first. Preserve all ranked-combo grading for proof visibility.
        rank_one = next((row for row in ranked_combo_results if row.get("rank") == 1), ranked_combo_results[0])
        status = rank_one.get("status") or status

    return {
        "PK": parlay.get("PK"),
        "SK": parlay.get("SK"),
        "prediction_type": parlay.get("prediction_type"),
        "asof": parlay.get("asof"),
        "status": status,
        "legs": graded_legs,
        "ranked_combos": ranked_combo_results,
    }


def grade_mlb_parlays(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    items = _predictions_for_slate(slate_date)
    parlays = [item for item in items if _is_parlay_item(item)]
    graded = []
    for parlay in parlays:
        result = _grade_parlay_item(parlay, slate_date)
        status = result.get("status") or "PENDING"
        predictions_tbl.update_item(
            Key={"PK": parlay["PK"], "SK": parlay["SK"]},
            UpdateExpression="SET #s=:s, evaluated_at=:e, settlement=:v, settlement_version=:sv",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":e": _now_iso(), ":v": _ddb_safe(result), ":sv": SETTLEMENT_VERSION},
        )
        graded.append(_jsonable(result))
    counts = _count_statuses(graded, field="status")
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "settlement_version": SETTLEMENT_VERSION,
        "frozen_parlay_count": len(parlays),
        "status_counts": counts,
        "parlays": graded,
        "message": "NO_FROZEN_PARLAYS_TO_GRADE" if not parlays else "FROZEN_PARLAYS_GRADED",
    }


def _count_statuses(rows: List[Dict[str, Any]], field: str = "status") -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return counts


def final_mlb_scores_report(slate_date: Optional[str] = None, days_from: int = 3, fetch_scores: bool = True) -> Dict[str, Any]:
    slate_date = slate_date or _slate_date_et()
    fetch_report: Dict[str, Any] = {"ok": True, "skipped": True, "reason": "fetch_scores_false"}
    if fetch_scores:
        fetch_report = pull_mlb_results(days_from=days_from)
    outcomes = _outcomes_for_slate(slate_date)
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "settlement_version": SETTLEMENT_VERSION,
        "fetch_report": fetch_report,
        "final_score_count": len(outcomes),
        "final_scores": _jsonable(outcomes),
    }


def settle_mlb_slate(slate_date: Optional[str] = None, days_from: int = 3, fetch_scores: bool = True) -> Dict[str, Any]:
    slate_date = slate_date or _slate_date_et()
    score_report = final_mlb_scores_report(slate_date=slate_date, days_from=days_from, fetch_scores=fetch_scores)
    prediction_report = evaluate_mlb_predictions(slate_date=slate_date)
    parlay_report = grade_mlb_parlays(slate_date=slate_date)
    overall = "PENDING"
    if parlay_report.get("frozen_parlay_count"):
        if parlay_report.get("status_counts", {}).get("WON"):
            overall = "WON_PRESENT"
        elif parlay_report.get("status_counts", {}).get("LOST"):
            overall = "LOST_PRESENT"
    elif prediction_report.get("evaluated"):
        overall = "INDIVIDUAL_PICKS_GRADED"
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "settlement_version": SETTLEMENT_VERSION,
        "overall_status": overall,
        "mlbPostgameSettlement": {
            "enabled": True,
            "final_only": True,
            "live_or_in_progress_policy": "DO_NOT_GRADE_UNTIL_COMPLETED_TRUE",
            "score_fetch": score_report,
            "individual_predictions": prediction_report,
            "parlays": parlay_report,
        },
    }


def settlement_proof_report(slate_date: Optional[str] = None, days_from: int = 3, fetch_scores: bool = False) -> Dict[str, Any]:
    slate_date = slate_date or _slate_date_et()
    report = settle_mlb_slate(slate_date=slate_date, days_from=days_from, fetch_scores=fetch_scores)
    report["proof_report_type"] = "MLB_B1_0_POSTGAME_SETTLEMENT"
    report["notes"] = [
        "Settlement is reporting/grading only and does not change pregame ranking logic.",
        "Only games with completed=true and stored final scores are graded.",
        "Moneyline and run-line picks are graded at the leg level; frozen parlay rows are graded WON/LOST/PENDING.",
        "If no frozen parlay rows exist for the slate, NO_FROZEN_PARLAYS_TO_GRADE is returned while individual picks can still be graded.",
    ]
    return report


def mlb_training_export(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    items = _predictions_for_slate(slate_date)
    rows: List[Dict[str, Any]] = []
    for pred in items:
        if not _is_individual_prediction(pred):
            continue
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
            "home_delta": float(pred.get("home_delta", 0)) if pred.get("home_delta") is not None else 0,
            "away_delta": float(pred.get("away_delta", 0)) if pred.get("away_delta") is not None else 0,
            "spread_delta": float(pred.get("spread_delta", 0)) if pred.get("spread_delta") is not None else None,
            "total_delta": float(pred.get("total_delta", 0)) if pred.get("total_delta") is not None else None,
            "confidence": float(pred.get("confidence", 0)) if pred.get("confidence") is not None else 0,
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
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "row_count": len(rows), "rows": _jsonable(rows)}
