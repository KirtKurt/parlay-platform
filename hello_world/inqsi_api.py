import json
from typing import Any, Dict, List

from inqsi_core import InqsiError, active_sport_keys, analyze_sport, auto_parlay, discover_sports, game_detail, graph_data, json_default, latest_game_states, pull_and_analyze_all, pull_and_analyze_sport, user_parlay


def response(status: int, body: Any) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,OPTIONS"}, "body": json.dumps(body, default=json_default)}


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(event.get("body") or "{}")
    except Exception:
        return {}


def request_path(event: Dict[str, Any]) -> str:
    return (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    if event.get("httpMethod") == "OPTIONS":
        return response(200, {"ok": True})
    try:
        p = request_path(event)
        q = event.get("queryStringParameters") or {}
        body = parse_body(event)
        sport = q.get("sport_key") or body.get("sport_key")
        game_id = q.get("game_id") or body.get("game_id")

        if p.endswith("/health"):
            return response(200, {"ok": True, "service": "inqsi-backend", "version": "v1"})
        if p.endswith("/sports"):
            return response(200, {"ok": True, "configured_sports": active_sport_keys(), "available_sports": discover_sports()})
        if p.endswith("/pull"):
            return response(200, pull_and_analyze_sport(sport) if sport else pull_and_analyze_all())
        if p.endswith("/games"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, {"ok": True, "sport_key": sport, "games": latest_game_states(sport)})
        if p.endswith("/analyze"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, analyze_sport(sport, store=str(q.get("store") or body.get("store") or "false").lower() == "true"))
        if p.endswith("/game"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            return response(200, game_detail(sport, game_id))
        if p.endswith("/graph"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            window = q.get("window") or body.get("window") or "full"
            return response(200, graph_data(sport, game_id, window))
        if p.endswith("/auto-parlay"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required. Auto-parlay is sport-isolated and cannot mix sports."})
            return response(200, auto_parlay(sport))
        if p.endswith("/user-parlay"):
            game_ids: List[str] = body.get("game_ids") or []
            if not sport or len(game_ids) != 3:
                return response(400, {"ok": False, "error": "sport_key and exactly three game_ids are required"})
            return response(200, user_parlay(sport, game_ids))
        return response(404, {"ok": False, "error": "route not found", "path": p})
    except InqsiError as exc:
        return response(400, {"ok": False, "error": str(exc)})
    except Exception as exc:
        return response(500, {"ok": False, "error": str(exc)})
