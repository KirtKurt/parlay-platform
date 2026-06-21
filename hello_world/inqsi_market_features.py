import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from inqsi_core import latest_game_states, latest_snapshot, rank_combinations
from inqsi_public_predictions import public_predictions_for_sport


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


def _american_value(price: Any, side: str = "favorite") -> Optional[int]:
    try:
        return int(price)
    except Exception:
        return None


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


def save_watchlist_item(user_id: str, sport_key: str, game_id: str) -> Dict[str, Any]:
    if _signal_ledger is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    item = {"PK": f"INQSI#WATCHLIST#{user_id}", "SK": f"SPORT#{sport_key}#GAME#{game_id}", "entity_type": "INQSI_WATCHLIST_ITEM", "user_id": user_id, "sport_key": sport_key, "game_id": game_id, "created_at": now_iso(), "status": "ACTIVE"}
    _signal_ledger.put_item(Item=_to_ddb(item))
    return {"ok": True, "watchlist_item": item}


def watchlist(user_id: str) -> Dict[str, Any]:
    if _signal_ledger is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    response = _signal_ledger.query(KeyConditionExpression=Key("PK").eq(f"INQSI#WATCHLIST#{user_id}"))
    return {"ok": True, "user_id": user_id, "items": [_from_ddb(i) for i in response.get("Items", [])]}


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
    return {"ok": True, "sport_key": sport_key, "alerts": alerts, "count": len(alerts)}


def public_performance_dashboard(sport_key: str) -> Dict[str, Any]:
    if _signal_ledger is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    # Dashboard is sport-scoped only. It reads learning records created by nightly autopsy.
    winner_resp = _signal_ledger.query(KeyConditionExpression=Key("PK").eq(f"INQSI#WINNER_LEARNING#{sport_key}"))
    parlay_resp = _signal_ledger.query(KeyConditionExpression=Key("PK").eq(f"INQSI#AUTOPSY#{sport_key}"))
    winner_items = [_from_ddb(i) for i in winner_resp.get("Items", [])]
    parlay_items = [_from_ddb(i) for i in parlay_resp.get("Items", [])]
    winner_total = len(winner_items)
    winner_hits = len([i for i in winner_items if i.get("prediction_hit") is True])
    parlay_total = len(parlay_items)
    top3 = len([i for i in parlay_items if i.get("top_3_containment") is True])
    top4 = len([i for i in parlay_items if i.get("top_4_containment") is True])
    return {"ok": True, "sport_key": sport_key, "winner_prediction_accuracy": round(winner_hits / winner_total, 4) if winner_total else None, "winner_predictions_graded": winner_total, "top_3_parlay_containment": round(top3 / parlay_total, 4) if parlay_total else None, "top_4_parlay_containment": round(top4 / parlay_total, 4) if parlay_total else None, "auto_parlays_graded": parlay_total}


def closing_line_value_stub(sport_key: str, game_id: str) -> Dict[str, Any]:
    # CLV requires capturing prediction line at publish time and the final pre-start/closing line.
    # This endpoint reserves the contract and keeps the data sport-scoped.
    return {"ok": True, "sport_key": sport_key, "game_id": game_id, "status": "CLV_CONTRACT_READY", "required_fields": ["published_line", "published_at", "closing_line", "closed_at", "beat_close"]}


def context_layer_stub(sport_key: str, game_id: str) -> Dict[str, Any]:
    # Injury, weather, lineup, goalie/pitcher/starter and news providers plug in here.
    return {"ok": True, "sport_key": sport_key, "game_id": game_id, "context_sources": ["injuries", "weather", "lineups", "starters", "news"], "status": "PROVIDER_HOOKS_READY"}
