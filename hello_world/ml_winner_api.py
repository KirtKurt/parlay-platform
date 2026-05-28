import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key

from ml_winner_engine import all_sport_winner_requirements, score_rule_based_winner_candidate


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None

PANEL_BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars"]
SUPPORTED_SNAPSHOT_SPORTS = {"mlb", "nba", "ncaam"}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _american_to_prob(american: int) -> float:
    return abs(american) / (abs(american) + 100.0) if american < 0 else 100.0 / (american + 100.0)


def _vig_norm(home_american: int, away_american: int) -> Tuple[float, float]:
    home_raw = _american_to_prob(home_american)
    away_raw = _american_to_prob(away_american)
    total = home_raw + away_raw
    return (home_raw / total, away_raw / total) if total else (0.5, 0.5)


def _asof_ts(snapshot: Dict[str, Any]) -> str:
    return snapshot.get("asof") or snapshot.get("created_at") or ""


def _recent_snapshots(sport: str, limit: int) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    response = snapshots_tbl.query(
        KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}"),
        ScanIndexForward=False,
        Limit=limit,
    )
    return sorted(response.get("Items", []), key=_asof_ts)


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    games = snapshot.get("data", {}).get("games", []) or []
    return {g.get("game_key") or g.get("id"): g for g in games if g.get("game_key") or g.get("id")}


def _panel_probs(game: Dict[str, Any]) -> Dict[str, float]:
    home_vals: List[float] = []
    away_vals: List[float] = []
    books = game.get("books", {}) or {}
    for book in PANEL_BOOKS:
        ml = (books.get(book) or {}).get("ml")
        if not ml:
            continue
        home_p, away_p = _vig_norm(int(ml["home"]), int(ml["away"]))
        home_vals.append(home_p)
        away_vals.append(away_p)
    if not home_vals:
        for data in books.values():
            ml = (data or {}).get("ml")
            if ml:
                home_p, away_p = _vig_norm(int(ml["home"]), int(ml["away"]))
                home_vals.append(home_p)
                away_vals.append(away_p)
                break
    if not home_vals:
        return {}
    return {"home": sum(home_vals) / len(home_vals), "away": sum(away_vals) / len(away_vals), "book_count": len(home_vals)}


def _spread_snapshot(game: Dict[str, Any]) -> Optional[Dict[str, float]]:
    books = game.get("books", {}) or {}
    for book in PANEL_BOOKS:
        spread = (books.get(book) or {}).get("spread")
        if spread:
            return spread
    for data in books.values():
        spread = (data or {}).get("spread")
        if spread:
            return spread
    return None


def _total_snapshot(game: Dict[str, Any]) -> Optional[Dict[str, float]]:
    books = game.get("books", {}) or {}
    for book in PANEL_BOOKS:
        total = (books.get(book) or {}).get("total")
        if total:
            return total
    for data in books.values():
        total = (data or {}).get("total")
        if total:
            return total
    return None


def _pick_hot_side(latest_game: Dict[str, Any], previous_game: Dict[str, Any], base_game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    latest_probs = _panel_probs(latest_game)
    previous_probs = _panel_probs(previous_game)
    base_probs = _panel_probs(base_game)
    if not latest_probs or not previous_probs or not base_probs:
        return None

    home_delta_15 = latest_probs["home"] - previous_probs["home"]
    away_delta_15 = latest_probs["away"] - previous_probs["away"]
    home_delta_open = latest_probs["home"] - base_probs["home"]
    away_delta_open = latest_probs["away"] - base_probs["away"]

    if abs(home_delta_15) >= abs(away_delta_15):
        side = "home"
        team = latest_game.get("home_team")
        delta_15 = home_delta_15
        delta_open = home_delta_open
        opp_delta_15 = away_delta_15
    else:
        side = "away"
        team = latest_game.get("away_team")
        delta_15 = away_delta_15
        delta_open = away_delta_open
        opp_delta_15 = home_delta_15

    spread_latest = _spread_snapshot(latest_game)
    spread_prev = _spread_snapshot(previous_game)
    total_latest = _total_snapshot(latest_game)
    total_prev = _total_snapshot(previous_game)

    spread_disagreement = False
    spread_confirmation = False
    if spread_latest and spread_prev:
        if side == "home":
            spread_move = float(spread_latest.get("home_point", 0)) - float(spread_prev.get("home_point", 0))
        else:
            spread_move = float(spread_latest.get("away_point", 0)) - float(spread_prev.get("away_point", 0))
        spread_confirmation = delta_15 > 0 and spread_move < 0
        spread_disagreement = delta_15 > 0 and spread_move > 0
    else:
        spread_move = 0.0

    total_confirmation = False
    total_move = 0.0
    if total_latest and total_prev:
        total_move = float(total_latest.get("over_point", 0)) - float(total_prev.get("over_point", 0))
        total_confirmation = abs(total_move) >= 0.5

    favorite_not_separating = latest_probs["home"] < 0.58 and latest_probs["away"] < 0.58
    dog_tightening = delta_15 > 0 and (latest_probs[side] < 0.50 or latest_probs[side] < max(latest_probs["home"], latest_probs["away"]))
    multi_book_agreement = int(latest_probs.get("book_count", 0)) >= 2
    moneyline_steam = delta_15 > 0.005
    late_reversal = delta_15 < 0 and delta_open > 0

    base_confidence = 55 + abs(delta_15) * 900 + max(0, delta_open) * 350
    signal = {
        "sport": latest_game.get("sport"),
        "game_key": latest_game.get("game_key") or latest_game.get("id"),
        "predicted_team": team,
        "predicted_side": side,
        "confidence": round(base_confidence, 2),
        "multi_book_agreement": multi_book_agreement,
        "moneyline_steam": moneyline_steam,
        "spread_confirmation": spread_confirmation,
        "spread_disagreement": spread_disagreement,
        "total_confirmation": total_confirmation,
        "dog_tightening": dog_tightening,
        "favorite_not_separating": favorite_not_separating,
        "late_reversal": late_reversal,
        "home_delta_15": round(home_delta_15, 5),
        "away_delta_15": round(away_delta_15, 5),
        "home_delta_open": round(home_delta_open, 5),
        "away_delta_open": round(away_delta_open, 5),
        "spread_move": round(spread_move, 3),
        "total_move": round(total_move, 3),
    }
    return signal


def winner_predictions(sport: str, limit: int = 60) -> Dict[str, Any]:
    sport = sport.lower()
    if sport not in SUPPORTED_SNAPSHOT_SPORTS:
        return {
            "ok": False,
            "error": "Sport is not enabled for snapshot-based winner predictions yet.",
            "sport": sport,
            "enabled_sports": sorted(SUPPORTED_SNAPSHOT_SPORTS),
        }

    snapshots = _recent_snapshots(sport, limit)
    if len(snapshots) < 2:
        return {"ok": True, "sport": sport, "predictions": [], "message": "Need at least two snapshots for rolling-window winner predictions."}

    base = snapshots[0]
    previous = snapshots[-2]
    latest = snapshots[-1]
    base_games = _game_index(base)
    prev_games = _game_index(previous)
    latest_games = _game_index(latest)

    predictions: List[Dict[str, Any]] = []
    for game_key, latest_game in latest_games.items():
        previous_game = prev_games.get(game_key)
        base_game = base_games.get(game_key) or previous_game
        if not previous_game or not base_game:
            continue
        latest_game = dict(latest_game)
        latest_game["sport"] = sport
        raw_signal = _pick_hot_side(latest_game, previous_game, base_game)
        if not raw_signal:
            continue
        scored = score_rule_based_winner_candidate(raw_signal)
        predictions.append({
            "sport": sport,
            "game_key": game_key,
            "game_id": latest_game.get("id"),
            "home_team": latest_game.get("home_team"),
            "away_team": latest_game.get("away_team"),
            "commence_time": latest_game.get("commence_time"),
            "prediction_status": scored.prediction_status,
            "predicted_team": scored.predicted_team,
            "confidence": scored.confidence,
            "confidence_label": scored.confidence_label,
            "reason_codes": scored.reason_codes,
            "user_message": scored.user_message,
            "movement_windows": {
                "latest_asof": latest.get("asof"),
                "previous_asof": previous.get("asof"),
                "base_asof": base.get("asof"),
                "last_snapshot_delta": {
                    "home": raw_signal["home_delta_15"],
                    "away": raw_signal["away_delta_15"],
                },
                "since_base_delta": {
                    "home": raw_signal["home_delta_open"],
                    "away": raw_signal["away_delta_open"],
                },
                "spread_move": raw_signal["spread_move"],
                "total_move": raw_signal["total_move"],
            },
            "raw_features": raw_signal,
        })

    predictions.sort(key=lambda row: (row["prediction_status"] == "PUBLISHED", row["confidence"]), reverse=True)
    published = [p for p in predictions if p["prediction_status"] == "PUBLISHED"]
    watchlist = [p for p in predictions if p["prediction_status"] == "WATCHLIST"]
    no_edge = [p for p in predictions if p["prediction_status"] == "NO_EDGE"]

    return {
        "ok": True,
        "sport": sport,
        "model": "rolling-window-winner-v0",
        "scope": "individual_game_winner_only",
        "target_accuracy_pct": 65,
        "important_rule": "Do not force picks. Published accuracy only counts PUBLISHED predictions.",
        "snapshot_count_used": len(snapshots),
        "counts": {"published": len(published), "watchlist": len(watchlist), "no_edge": len(no_edge), "total": len(predictions)},
        "predictions": predictions,
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "GET").upper()
    path = event.get("path") or "/"
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    if method == "GET" and path == "/v1/ml/winners/requirements":
        return _resp(200, all_sport_winner_requirements())
    if method == "GET" and path == "/v1/ml/winners":
        params = event.get("queryStringParameters") or {}
        sport = (params.get("sport") or "mlb").lower()
        limit = min(max(int(params.get("limit") or 60), 2), 300)
        try:
            return _resp(200, winner_predictions(sport, limit))
        except Exception as exc:
            return _resp(500, {"ok": False, "sport": sport, "error": str(exc)})
    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
