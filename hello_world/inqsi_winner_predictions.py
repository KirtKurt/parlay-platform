import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_core import latest_game_states
from inqsi_live import pull_scores_for_sport


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


PREDICTIONS_TABLE = _env("PREDICTIONS_TABLE")
SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")

_dynamodb = boto3.resource("dynamodb")
_predictions = _dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value]
    return value


def _from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_ddb(v) for v in value]
    return value


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def visible_at(commence_time: Optional[str]) -> Optional[str]:
    dt = parse_dt(commence_time)
    if not dt:
        return None
    return (dt - timedelta(hours=1)).astimezone(timezone.utc).isoformat()


def is_visible(prediction: Dict[str, Any]) -> bool:
    va = parse_dt(prediction.get("visible_at"))
    return bool(va and now_utc() >= va)


def _prediction_side(game: Dict[str, Any]) -> str:
    direction = (game.get("market_direction") or {}).get("side")
    if direction in {"home", "away"}:
        return direction
    scores = game.get("side_scores") or {}
    return "home" if scores.get("home", 0) >= scores.get("away", 0) else "away"


def build_winner_prediction(game: Dict[str, Any], model_version: str = "INQSI_WINNER_V1") -> Dict[str, Any]:
    side = _prediction_side(game)
    side_scores = game.get("side_scores") or {}
    team = game.get("home_team") if side == "home" else game.get("away_team")
    confidence = int(max(1, min(99, side_scores.get(side, game.get("signal_score", 50)))))
    prediction_id = f"{game.get('sport_key')}#{game.get('game_id')}#{game.get('asof')}"
    return {
        "PK": f"INQSI#WINNER_LATEST#{game.get('sport_key')}",
        "SK": f"GAME#{game.get('game_id')}",
        "entity_type": "INQSI_WINNER_PREDICTION",
        "sport_key": game.get("sport_key"),
        "game_id": game.get("game_id"),
        "prediction_id": prediction_id,
        "status": "OPEN",
        "model_version": model_version,
        "created_at": now_iso(),
        "asof": game.get("asof"),
        "visible_at": visible_at(game.get("commence_time")),
        "commence_time": game.get("commence_time"),
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "predicted_side": side,
        "predicted_winner": team,
        "confidence_score": confidence,
        "primary_signal": game.get("primary_signal"),
        "signal_score": game.get("signal_score"),
        "stability_classification": game.get("stability_classification"),
        "short_explanation": f"InQsi currently leans {team} based on market direction, signal score, and stability classification.",
        "what_to_watch": game.get("what_looks_wrong"),
    }


def store_winner_predictions_for_sport(sport_key: str) -> Dict[str, Any]:
    if _predictions is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    games = latest_game_states(sport_key)
    written = 0
    for game in games:
        if game.get("sport_key") != sport_key:
            raise RuntimeError("Sport isolation violation in winner prediction generation")
        if not game.get("commence_time"):
            continue
        item = build_winner_prediction(game)
        _predictions.put_item(Item=_to_ddb(item))
        _predictions.put_item(Item=_to_ddb({**item, "PK": f"INQSI#WINNER_HISTORY#{sport_key}", "SK": f"PRED#{item['created_at']}#GAME#{game.get('game_id')}"}))
        written += 1
    return {"ok": True, "sport_key": sport_key, "predictions_written": written}


def visible_winner_predictions(sport_key: str) -> Dict[str, Any]:
    if _predictions is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    response = _predictions.query(KeyConditionExpression=Key("PK").eq(f"INQSI#WINNER_LATEST#{sport_key}"))
    predictions = [_from_ddb(item) for item in response.get("Items", [])]
    visible = [p for p in predictions if is_visible(p)]
    visible.sort(key=lambda p: p.get("commence_time") or "")
    return {"ok": True, "sport_key": sport_key, "visible_rule": "1_hour_before_event_start", "predictions": visible, "count": len(visible)}


def _score_winners(scores: List[Dict[str, Any]]) -> Dict[str, str]:
    winners = {}
    for game in scores:
        if game.get("completed") is not True:
            continue
        game_id = game.get("id")
        rows = game.get("scores") or []
        if not game_id or len(rows) < 2:
            continue
        try:
            sorted_rows = sorted(rows, key=lambda x: int(x.get("score", 0)), reverse=True)
            winners[game_id] = sorted_rows[0].get("name")
        except Exception:
            continue
    return winners


def grade_winner_predictions_for_sport(sport_key: str) -> Dict[str, Any]:
    if _predictions is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    scores = pull_scores_for_sport(sport_key, days_from=3)
    winners = _score_winners(scores)
    response = _predictions.query(KeyConditionExpression=Key("PK").eq(f"INQSI#WINNER_LATEST#{sport_key}"))
    checked = graded = 0
    for raw in response.get("Items", []):
        item = _from_ddb(raw)
        if item.get("status") == "GRADED":
            continue
        game_id = item.get("game_id")
        if game_id not in winners:
            continue
        checked += 1
        actual = winners[game_id]
        hit = actual == item.get("predicted_winner")
        graded_item = {**item, "status": "GRADED", "graded_at": now_iso(), "actual_winner": actual, "prediction_hit": hit}
        _predictions.put_item(Item=_to_ddb(graded_item))
        learning = {
            "PK": f"INQSI#WINNER_LEARNING#{sport_key}",
            "SK": f"RESULT#{graded_item['graded_at']}#GAME#{game_id}",
            "entity_type": "INQSI_WINNER_LEARNING_RECORD",
            "sport_key": sport_key,
            "game_id": game_id,
            "model_version": item.get("model_version"),
            "predicted_winner": item.get("predicted_winner"),
            "actual_winner": actual,
            "prediction_hit": hit,
            "confidence_score": item.get("confidence_score"),
            "primary_signal": item.get("primary_signal"),
            "signal_score": item.get("signal_score"),
            "stability_classification": item.get("stability_classification"),
            "created_at": now_iso(),
        }
        _predictions.put_item(Item=_to_ddb(learning))
        graded += 1
    return {"ok": True, "sport_key": sport_key, "checked": checked, "graded": graded}
