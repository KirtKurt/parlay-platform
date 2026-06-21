import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_core import auto_parlay, latest_game_states, latest_snapshot, rank_combinations
from inqsi_live import latest_live_games
from inqsi_winner_predictions import visible_winner_predictions


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


SIGNAL_LEDGER_TABLE = _env("SIGNAL_LEDGER_TABLE")
PREDICTIONS_TABLE = _env("PREDICTIONS_TABLE")

_dynamodb = boto3.resource("dynamodb")
_signal_ledger = _dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
_predictions = _dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _table():
    table = _signal_ledger or _predictions
    if table is None:
        raise RuntimeError("No InQsi market feature table configured")
    return table


def _better_moneyline(current: Optional[Dict[str, Any]], candidate: Dict[str, Any]) -> Dict[str, Any]:
    if current is None:
        return candidate
    return candidate if int(candidate["price"]) > int(current["price"]) else current


def best_available_lines(sport_key: str, game_id: str) -> Dict[str, Any]:
    snap = latest_snapshot(sport_key)
    if not snap:
        return {"ok": False, "sport_key": sport_key, "game_id": game_id, "error": "No snapshot available"}
    games = {g.get("game_id"): g for g in snap.get("data", {}).get("games", [])}
    game = games.get(game_id)
    if not game:
        return {"ok": False, "sport_key": sport_key, "game_id": game_id, "error": "Game not found"}
    if game.get("sport_key") != sport_key:
        raise RuntimeError("Sport isolation violation in best available lines")
    best = {"moneyline": {"home": None, "away": None}, "spread": {"home": None, "away": None}, "total": {"over": None, "under": None}}
    for book_key, book in (game.get("books") or {}).items():
        ml = book.get("moneyline") or {}
        if ml.get("home") is not None:
            best["moneyline"]["home"] = _better_moneyline(best["moneyline"]["home"], {"book_key": book_key, "book_title": book.get("title"), "price": ml.get("home")})
        if ml.get("away") is not None:
            best["moneyline"]["away"] = _better_moneyline(best["moneyline"]["away"], {"book_key": book_key, "book_title": book.get("title"), "price": ml.get("away")})
        spread = book.get("spread") or {}
        if spread.get("home_point") is not None:
            candidate = {"book_key": book_key, "book_title": book.get("title"), "point": spread.get("home_point"), "price": spread.get("home_price")}
            current = best["spread"]["home"]
            best["spread"]["home"] = candidate if current is None or float(candidate["point"]) > float(current["point"]) else current
        if spread.get("away_point") is not None:
            candidate = {"book_key": book_key, "book_title": book.get("title"), "point": spread.get("away_point"), "price": spread.get("away_price")}
            current = best["spread"]["away"]
            best["spread"]["away"] = candidate if current is None or float(candidate["point"]) > float(current["point"]) else current
        total = book.get("total") or {}
        if total.get("over_point") is not None:
            candidate = {"book_key": book_key, "book_title": book.get("title"), "point": total.get("over_point"), "price": total.get("over_price")}
            current = best["total"]["over"]
            best["total"]["over"] = candidate if current is None or float(candidate["point"]) < float(current["point"]) else current
        if total.get("under_point") is not None:
            candidate = {"book_key": book_key, "book_title": book.get("title"), "point": total.get("under_point"), "price": total.get("under_price")}
            current = best["total"]["under"]
            best["total"]["under"] = candidate if current is None or float(candidate["point"]) > float(current["point"]) else current
    return {"ok": True, "sport_key": sport_key, "game_id": game_id, "home_team": game.get("home_team"), "away_team": game.get("away_team"), "asof": snap.get("asof"), "best_available_lines": best}


def check_bet_slip(sport_key: str, legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(legs) != 3:
        return {"ok": False, "error": "Bet slip checker currently requires exactly three legs"}
    states = {g.get("game_id"): g for g in latest_game_states(sport_key)}
    checked = []
    for leg in legs:
        game_id = leg.get("game_id")
        game = states.get(game_id)
        if not game:
            checked.append({**leg, "found": False, "error": "Game not found for this sport"})
            continue
        if game.get("sport_key") != sport_key:
            raise RuntimeError("Sport isolation violation in bet slip checker")
        side = leg.get("side") or leg.get("picked_side")
        side_scores = game.get("side_scores") or {}
        score = side_scores.get(side, game.get("signal_score")) if side in {"home", "away"} else game.get("signal_score")
        checked.append({"found": True, "game_id": game_id, "picked_side": side, "picked_team": leg.get("team"), "signal_score": score, "primary_signal": game.get("primary_signal"), "stability": game.get("stability_classification"), "what_looks_wrong": game.get("what_looks_wrong")})
    valid_games = [states[l.get("game_id")] for l in legs if l.get("game_id") in states]
    ranking = rank_combinations(valid_games) if len(valid_games) == 3 else []
    weakest = min(checked, key=lambda x: x.get("signal_score") or 0) if checked else None
    strongest = max(checked, key=lambda x: x.get("signal_score") or 0) if checked else None
    return {"ok": True, "sport_key": sport_key, "legs_checked": checked, "strongest_leg": strongest, "weakest_leg": weakest, "ranked_combinations": ranking, "message": "Scan your ticket. Find what looks wrong."}


def save_bet_slip_scan(user_id: str, sport_key: str, legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = check_bet_slip(sport_key, legs)
    scan_id = f"SCAN#{sport_key}#{now_iso()}#{uuid.uuid4().hex[:8]}"
    item = {"PK": f"INQSI#USER#{user_id}", "SK": scan_id, "entity_type": "INQSI_BET_SLIP_SCAN", "user_id": user_id, "sport_key": sport_key, "created_at": now_iso(), "legs": legs, "scan_result": result}
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "scan_id": scan_id, "result": result}


def save_watchlist_item(user_id: str, sport_key: str, game_id: str) -> Dict[str, Any]:
    item = {"PK": f"INQSI#USER#{user_id}", "SK": f"WATCH#{sport_key}#GAME#{game_id}", "entity_type": "INQSI_WATCHLIST_ITEM", "user_id": user_id, "sport_key": sport_key, "game_id": game_id, "created_at": now_iso(), "status": "ACTIVE", "alert_rules": ["steam", "reversal", "chaos", "prediction_visible", "one_hour_final_check"]}
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "watchlist_item": item}


def watchlist(user_id: str) -> Dict[str, Any]:
    response = _table().query(KeyConditionExpression=Key("PK").eq(f"INQSI#USER#{user_id}"))
    items = [_from_ddb(i) for i in response.get("Items", [])]
    return {"ok": True, "user_id": user_id, "items": [i for i in items if i.get("entity_type") == "INQSI_WATCHLIST_ITEM"], "bet_slip_scans": [i for i in items if i.get("entity_type") == "INQSI_BET_SLIP_SCAN"], "alerts": [i for i in items if i.get("entity_type") == "INQSI_ALERT"]}


def alert_candidates(sport_key: str) -> Dict[str, Any]:
    alerts = []
    for game in latest_game_states(sport_key):
        if game.get("sport_key") != sport_key:
            raise RuntimeError("Sport isolation violation in alert engine")
        indicators = game.get("indicators") or {}
        if indicators.get("steam"):
            alerts.append({"type": "STEAM_DETECTED", "sport_key": sport_key, "game_id": game.get("game_id"), "message": f"Steam detected: {game.get('home_team')} vs {game.get('away_team')}."})
        if indicators.get("reversal"):
            alerts.append({"type": "REVERSAL_WARNING", "sport_key": sport_key, "game_id": game.get("game_id"), "message": "Reversal warning detected. Review before locking it in."})
        if indicators.get("chaos"):
            alerts.append({"type": "CHAOS_ALERT", "sport_key": sport_key, "game_id": game.get("game_id"), "message": "Chaos alert: books or movement are unstable."})
    return {"ok": True, "sport_key": sport_key, "alerts": alerts, "count": len(alerts), "push_status": "backend_ready_mobile_push_provider_needed"}


def create_alert(user_id: str, sport_key: str, game_id: str, alert_type: str, message: str) -> Dict[str, Any]:
    item = {"PK": f"INQSI#USER#{user_id}", "SK": f"ALERT#{now_iso()}#{uuid.uuid4().hex[:8]}", "entity_type": "INQSI_ALERT", "user_id": user_id, "sport_key": sport_key, "game_id": game_id, "alert_type": alert_type, "message": message, "status": "UNREAD", "created_at": now_iso()}
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "alert": item}


def user_dashboard(user_id: str, sport_key: Optional[str] = None) -> Dict[str, Any]:
    data = watchlist(user_id)
    payload = {"ok": True, "user_id": user_id, "watchlist": data.get("items", []), "bet_slip_scans": data.get("bet_slip_scans", []), "alerts": data.get("alerts", [])}
    if sport_key:
        payload["visible_winner_predictions"] = visible_winner_predictions(sport_key)
        payload["auto_parlay"] = auto_parlay(sport_key)
    return payload


def live_market_mode(sport_key: str) -> Dict[str, Any]:
    live = latest_live_games(sport_key)
    states = {g.get("game_id"): g for g in latest_game_states(sport_key)}
    enhanced = []
    for game in live.get("games", []):
        enhanced.append({**game, "signal_state": states.get(game.get("game_id"))})
    return {"ok": True, "sport_key": sport_key, "mode": "live_market_mode", "games": enhanced, "visible_winner_predictions": visible_winner_predictions(sport_key)}


def public_performance_dashboard(sport_key: str) -> Dict[str, Any]:
    winner_resp = _table().query(KeyConditionExpression=Key("PK").eq(f"INQSI#WINNER_LEARNING#{sport_key}"))
    parlay_resp = _table().query(KeyConditionExpression=Key("PK").eq(f"INQSI#AUTOPSY#{sport_key}"))
    winner_items = [_from_ddb(i) for i in winner_resp.get("Items", [])]
    parlay_items = [_from_ddb(i) for i in parlay_resp.get("Items", [])]
    winner_total = len(winner_items)
    winner_hits = len([i for i in winner_items if i.get("prediction_hit") is True])
    parlay_total = len(parlay_items)
    top3 = len([i for i in parlay_items if i.get("top_3_containment") is True])
    top4 = len([i for i in parlay_items if i.get("top_4_containment") is True])
    return {"ok": True, "sport_key": sport_key, "winner_prediction_accuracy": round(winner_hits / winner_total, 4) if winner_total else None, "winner_predictions_graded": winner_total, "top_3_parlay_containment": round(top3 / parlay_total, 4) if parlay_total else None, "top_4_parlay_containment": round(top4 / parlay_total, 4) if parlay_total else None, "auto_parlays_graded": parlay_total}


def closing_line_value_record(sport_key: str, game_id: str, published_line: Dict[str, Any], closing_line: Dict[str, Any]) -> Dict[str, Any]:
    item = {"PK": f"INQSI#CLV#{sport_key}", "SK": f"GAME#{game_id}#ASOF#{now_iso()}", "entity_type": "INQSI_CLOSING_LINE_VALUE", "sport_key": sport_key, "game_id": game_id, "published_line": published_line, "closing_line": closing_line, "created_at": now_iso()}
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "clv_record": item}


def context_layer_stub(sport_key: str, game_id: str, context_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    item = {"PK": f"INQSI#CONTEXT#{sport_key}", "SK": f"GAME#{game_id}", "entity_type": "INQSI_GAME_CONTEXT", "sport_key": sport_key, "game_id": game_id, "updated_at": now_iso(), "context_items": context_items or [], "provider_hooks": ["injuries", "weather", "lineups", "starters", "news"]}
    _table().put_item(Item=_to_ddb(item))
    return {"ok": True, "context": item, "status": "provider_hooks_ready"}


def community_leaderboard_stub(sport_key: Optional[str] = None) -> Dict[str, Any]:
    return {"ok": True, "sport_key": sport_key, "feature": "community_leaderboard", "status": "foundation_ready", "note": "Requires user identity, verified slip rules, ranking rules, and abuse controls before public launch."}
