import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_core import active_sport_keys, latest_game_states
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


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _within_public_window(commence_time: Optional[str]) -> bool:
    start = _parse_dt(commence_time)
    if not start:
        return False
    seconds_to_start = (start - now_utc()).total_seconds()
    return 0 <= seconds_to_start <= 3600


def _choose_winner(game_state: Dict[str, Any]) -> Dict[str, Any]:
    side_scores = game_state.get("side_scores") or {}
    market_side = (game_state.get("market_direction") or {}).get("side")
    if market_side in {"home", "away"}:
        side = market_side
    else:
        side = "home" if side_scores.get("home", 0) >= side_scores.get("away", 0) else "away"
    team = game_state.get("home_team") if side == "home" else game_state.get("away_team")
    confidence = int(max(1, min(100, side_scores.get(side, game_state.get("signal_score", 50) or 50))))
    return {"predicted_side": side, "predicted_team": team, "confidence_score": confidence}


def _explanation(game_state: Dict[str, Any], pick: Dict[str, Any]) -> str:
    signal = game_state.get("primary_signal") or "Market signal"
    stability = game_state.get("stability_classification") or "Watch"
    team = pick.get("predicted_team")
    if stability == "Chaos":
        return f"{team} is the lean, but this game is flagged as unstable. Review before trusting the pick."
    if stability == "Unstable":
        return f"{team} is the lean, but recent market movement is unstable."
    return f"{team} is the lean based on {signal.lower()} and current market stability."


def generate_public_predictions_for_sport(sport_key: str) -> Dict[str, Any]:
    if _predictions is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    created = 0
    skipped = 0
    predictions: List[Dict[str, Any]] = []
    for game in latest_game_states(sport_key):
        if game.get("sport_key") != sport_key:
            raise RuntimeError(f"Sport isolation violation: expected {sport_key}, got {game.get('sport_key')}")
        if not _within_public_window(game.get("commence_time")):
            skipped += 1
            continue
        pick = _choose_winner(game)
        asof = now_iso()
        prediction_id = f"{sport_key}#{game.get('game_id')}#{game.get('commence_time')}"
        item = {
            "PK": f"INQSI#PUBLIC_PREDICTION#{sport_key}",
            "SK": f"GAME#{game.get('game_id')}",
            "entity_type": "INQSI_PUBLIC_WINNER_PREDICTION",
            "status": "OPEN",
            "sport_key": sport_key,
            "prediction_id": prediction_id,
            "game_id": game.get("game_id"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "commence_time": game.get("commence_time"),
            "visible_at": asof,
            "visibility_rule": "visible_one_hour_before_start",
            "predicted_side": pick.get("predicted_side"),
            "predicted_team": pick.get("predicted_team"),
            "confidence_score": pick.get("confidence_score"),
            "signal_score": game.get("signal_score"),
            "primary_signal": game.get("primary_signal"),
            "stability_classification": game.get("stability_classification"),
            "market_direction": game.get("market_direction"),
            "short_explanation": _explanation(game, pick),
            "result": {},
        }
        _predictions.put_item(Item=_to_ddb(item))
        predictions.append(item)
        created += 1
    return {"ok": True, "sport_key": sport_key, "created_or_updated": created, "skipped_not_inside_one_hour_window": skipped, "predictions": predictions}


def generate_public_predictions_all_sports() -> Dict[str, Any]:
    results = []
    for sport_key in active_sport_keys():
        try:
            results.append(generate_public_predictions_for_sport(sport_key))
        except Exception as exc:
            results.append({"ok": False, "sport_key": sport_key, "error": str(exc)})
    return {"ok": all(r.get("ok") for r in results), "sports_checked": len(results), "results": results}


def public_predictions_for_sport(sport_key: str) -> Dict[str, Any]:
    if _predictions is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    response = _predictions.query(KeyConditionExpression=Key("PK").eq(f"INQSI#PUBLIC_PREDICTION#{sport_key}"))
    items = [_from_ddb(item) for item in response.get("Items", [])]
    items.sort(key=lambda x: x.get("commence_time") or "")
    return {"ok": True, "sport_key": sport_key, "predictions": items, "count": len(items)}


def _score_winners(scores: List[Dict[str, Any]]) -> Dict[str, str]:
    winners: Dict[str, str] = {}
    for game in scores:
        if game.get("completed") is not True:
            continue
        teams = game.get("scores") or []
        if not game.get("id") or len(teams) < 2:
            continue
        try:
            sorted_scores = sorted(teams, key=lambda x: int(x.get("score", 0)), reverse=True)
            winners[game.get("id")] = sorted_scores[0].get("name")
        except Exception:
            continue
    return winners


def grade_public_predictions_for_sport(sport_key: str) -> Dict[str, Any]:
    if _predictions is None or _signal_ledger is None:
        raise RuntimeError("Prediction storage not configured")
    predictions = public_predictions_for_sport(sport_key).get("predictions", [])
    winners = _score_winners(pull_scores_for_sport(sport_key, days_from=3))
    graded = 0
    open_count = 0
    for prediction in predictions:
        if prediction.get("status") == "GRADED":
            continue
        open_count += 1
        winner = winners.get(prediction.get("game_id"))
        if not winner:
            continue
        hit = winner == prediction.get("predicted_team")
        result = {"graded_at": now_iso(), "actual_winner": winner, "prediction_hit": hit}
        updated = {**prediction, "status": "GRADED", "result": result}
        _predictions.put_item(Item=_to_ddb(updated))
        learning = {
            "PK": f"INQSI#WINNER_LEARNING#{sport_key}",
            "SK": f"RESULT#{result['graded_at']}#GAME#{prediction.get('game_id')}",
            "entity_type": "INQSI_WINNER_PREDICTION_RESULT",
            "sport_key": sport_key,
            "game_id": prediction.get("game_id"),
            "predicted_team": prediction.get("predicted_team"),
            "actual_winner": winner,
            "prediction_hit": hit,
            "confidence_score": prediction.get("confidence_score"),
            "signal_score": prediction.get("signal_score"),
            "primary_signal": prediction.get("primary_signal"),
            "stability_classification": prediction.get("stability_classification"),
            "graded_at": result["graded_at"],
        }
        _signal_ledger.put_item(Item=_to_ddb(learning))
        graded += 1
    return {"ok": True, "sport_key": sport_key, "open_checked": open_count, "graded": graded}
