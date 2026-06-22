import json
from typing import Any, Dict, List

from inqsi_core import InqsiError, active_sport_keys, analyze_sport, auto_parlay, discover_sports, game_detail, graph_data, json_default, latest_game_states, pull_and_analyze_all, pull_and_analyze_sport, user_parlay
from inqsi_live import ingest_live_sport, latest_live_games
from inqsi_winner_predictions import store_winner_predictions_for_sport, visible_winner_predictions
from inqsi_market_features import alert_candidates, best_available_lines, check_bet_slip, closing_line_value_record, community_leaderboard_stub, context_layer_stub, live_market_mode, public_performance_dashboard, save_bet_slip_scan, save_watchlist_item, user_dashboard, watchlist
from inqsi_runtime_features import access_check, build_parlay, build_signals, data_quality_check, manual_result_grade, normalize_market_data, scan_slip, store_manual_snapshot


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
        sport = q.get("sport_key") or q.get("sport") or body.get("sport_key") or body.get("sport")
        game_id = q.get("game_id") or body.get("game_id")
        user_id = q.get("user_id") or body.get("user_id") or body.get("memberId") or "anonymous"
        method = (event.get("httpMethod") or "GET").upper()

        if p in {"/v1/inqsi/market/snapshots", "/v1/market/snapshots"} and method == "POST":
            return response(201, store_manual_snapshot(body))
        if p in {"/v1/inqsi/market/normalize", "/v1/market/normalize"}:
            return response(200, normalize_market_data(body if body else {**q, "sport": sport}))
        if p in {"/v1/inqsi/signals/build", "/v1/signals/build"} and method == "POST":
            return response(200, build_signals(body))
        if p in {"/v1/inqsi/slip-scanner/scan", "/v1/slip-scanner/scan"} and method == "POST":
            return response(200, scan_slip({**body, "memberId": user_id}))
        if p in {"/v1/inqsi/parlays/build", "/v1/parlays/build"} and method == "POST":
            return response(200, build_parlay(body))
        if p in {"/v1/inqsi/results/grade-manual", "/v1/results/grade-manual"} and method == "POST":
            return response(200, manual_result_grade(body))
        if p in {"/v1/inqsi/access/check", "/v1/access/check"} and method == "POST":
            return response(200, access_check(body))
        if p in {"/v1/inqsi/monitoring/data-quality", "/v1/monitoring/data-quality"}:
            return response(200, data_quality_check())

        if p.endswith("/health"):
            return response(200, {"ok": True, "service": "inqsi-backend", "version": "v1", "nonOddsApiRuntime": True})
        if p.endswith("/sports"):
            return response(200, {"ok": True, "configured_sports": active_sport_keys(), "available_sports": discover_sports()})
        if p.endswith("/pull"):
            return response(200, pull_and_analyze_sport(sport) if sport else pull_and_analyze_all())
        if p.endswith("/live-pull"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, ingest_live_sport(sport))
        if p.endswith("/live-market"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, live_market_mode(sport))
        if p.endswith("/live"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, latest_live_games(sport))
        if p.endswith("/winner-predictions"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, visible_winner_predictions(sport))
        if p.endswith("/build-winner-predictions"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, store_winner_predictions_for_sport(sport))
        if p.endswith("/best-lines"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            return response(200, best_available_lines(sport, game_id))
        if p.endswith("/bet-slip-check"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            legs = body.get("legs") or []
            if user_id and user_id != "anonymous":
                return response(200, save_bet_slip_scan(user_id, sport, legs))
            return response(200, check_bet_slip(sport, legs))
        if p.endswith("/watchlist/add"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            return response(200, save_watchlist_item(user_id, sport, game_id))
        if p.endswith("/watchlist"):
            return response(200, watchlist(user_id))
        if p.endswith("/dashboard"):
            return response(200, user_dashboard(user_id, sport))
        if p.endswith("/alerts"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, alert_candidates(sport))
        if p.endswith("/performance"):
            if not sport:
                return response(400, {"ok": False, "error": "sport_key is required"})
            return response(200, public_performance_dashboard(sport))
        if p.endswith("/clv"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            return response(200, closing_line_value_record(sport, game_id, body.get("published_line") or {}, body.get("closing_line") or {}))
        if p.endswith("/context"):
            if not sport or not game_id:
                return response(400, {"ok": False, "error": "sport_key and game_id are required"})
            return response(200, context_layer_stub(sport, game_id, body.get("context_items") or []))
        if p.endswith("/leaderboard"):
            return response(200, community_leaderboard_stub(sport))
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
    except ValueError as exc:
        return response(400, {"ok": False, "error": str(exc)})
    except Exception as exc:
        return response(500, {"ok": False, "error": str(exc)})
