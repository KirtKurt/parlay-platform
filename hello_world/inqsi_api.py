import base64
import binascii
import json
from typing import Any, Dict, List, Optional, Tuple

from inqsi_core import InqsiError, active_sport_keys, analyze_sport, auto_parlay, discover_sports, game_detail, graph_data, json_default, latest_game_states, pull_and_analyze_all, pull_and_analyze_sport, user_parlay
from inqsi_live import ingest_live_sport, latest_live_games
from inqsi_winner_predictions import store_winner_predictions_for_sport, visible_winner_predictions
from inqsi_market_features import alert_candidates, best_available_lines, check_bet_slip, closing_line_value_record, community_leaderboard_stub, context_layer_stub, live_market_mode, public_performance_dashboard, save_bet_slip_scan, save_watchlist_item, user_dashboard, watchlist
from inqsi_runtime_features import access_check, build_parlay, build_signals, data_quality_check, manual_result_grade, normalize_market_data, scan_slip, store_manual_snapshot
from inqsi_pull_history import handle_pull_history_route


IMAGE_MODERATION_POLICY = {
    "ok": True,
    "service": "inqsi-image-moderation",
    "version": "v1",
    "mode": "pre_post_gate",
    "publishDefault": "quarantine_until_approved",
    "allow": [
        "clean_profile_photo",
        "clean_sports_photo",
        "clean_fan_photo_without_readable_text",
        "inqsi_generated_slip_cards",
        "inqsi_generated_badges_and_scores",
    ],
    "reject": [
        "any_uploaded_image_with_readable_text",
        "political_content",
        "racial_or_hateful_content",
        "profanity_or_obscene_gestures",
        "violence_weapons_gore_or_threats",
        "nudity_or_sexual_content",
        "drug_content",
        "memes_screenshots_watermarks_or_external_slogans",
    ],
    "rule": "User-uploaded images with words are rejected. Inqsi-generated text is allowed.",
}


REJECT_MODERATION_LABEL_TERMS = {
    "explicit nudity", "nudity", "sexual activity", "sexual situations", "suggestive",
    "violence", "graphic violence", "weapons", "weapon violence", "visually disturbing",
    "hate symbols", "drugs", "tobacco", "alcohol", "rude gestures", "middle finger",
}


def response(status: int, body: Any) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,OPTIONS"}, "body": json.dumps(body, default=json_default)}


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(event.get("body") or "{}")
    except Exception:
        return {}


def request_path(event: Dict[str, Any]) -> str:
    return (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"


def _decode_image_bytes(body: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    raw = body.get("image_base64") or body.get("imageBase64") or body.get("base64")
    if not raw:
        return None, "image_base64 is required"
    if isinstance(raw, str) and "," in raw and raw.strip().lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None, "image_base64 is not valid base64"
    if not data:
        return None, "image is empty"
    max_bytes = int(body.get("max_bytes") or body.get("maxBytes") or 5000000)
    if len(data) > max_bytes:
        return None, f"image exceeds {max_bytes} byte limit"
    return data, None


def _rekognition_scan(image_bytes: bytes, min_confidence: float = 70.0) -> Dict[str, Any]:
    try:
        import boto3
        client = boto3.client("rekognition")
        moderation = client.detect_moderation_labels(Image={"Bytes": image_bytes}, MinConfidence=float(min_confidence))
        text = client.detect_text(Image={"Bytes": image_bytes})
        return {
            "ok": True,
            "provider": "aws_rekognition",
            "moderationLabels": moderation.get("ModerationLabels", []),
            "textDetections": text.get("TextDetections", []),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "aws_rekognition",
            "errorType": type(exc).__name__,
            "error": str(exc),
        }


def _evaluate_image_scan(scan: Dict[str, Any], text_confidence_threshold: float = 70.0) -> Dict[str, Any]:
    if not scan.get("ok"):
        return {
            "approved": False,
            "decision": "MANUAL_REVIEW",
            "manual_review_required": True,
            "publish": False,
            "reason": "moderation_provider_not_available",
            "providerStatus": scan,
            "policy": IMAGE_MODERATION_POLICY,
        }

    text_hits = []
    for item in scan.get("textDetections") or []:
        detected = (item.get("DetectedText") or "").strip()
        confidence = float(item.get("Confidence") or 0)
        if detected and confidence >= text_confidence_threshold:
            text_hits.append({"text": detected, "confidence": round(confidence, 2), "type": item.get("Type")})

    moderation_hits = []
    for item in scan.get("moderationLabels") or []:
        name = (item.get("Name") or "").strip()
        parent = (item.get("ParentName") or "").strip()
        confidence = float(item.get("Confidence") or 0)
        lowered = {name.lower(), parent.lower()}
        if confidence >= 70 and (lowered & REJECT_MODERATION_LABEL_TERMS):
            moderation_hits.append({"name": name, "parent": parent, "confidence": round(confidence, 2)})

    violations = []
    if text_hits:
        violations.append({"rule": "reject_if_any_readable_text_detected", "matches": text_hits})
    if moderation_hits:
        violations.append({"rule": "reject_unsafe_or_brand_risk_image_content", "matches": moderation_hits})

    approved = not violations
    return {
        "approved": approved,
        "decision": "APPROVED" if approved else "REJECTED",
        "manual_review_required": False,
        "publish": approved,
        "reason": "passed_inqsi_image_policy" if approved else "violates_inqsi_image_policy",
        "violations": violations,
        "policy": IMAGE_MODERATION_POLICY,
        "provider": scan.get("provider"),
    }


def moderate_member_image(body: Dict[str, Any]) -> Dict[str, Any]:
    if body.get("policyOnly") or body.get("policy_only"):
        return IMAGE_MODERATION_POLICY
    image_bytes, err = _decode_image_bytes(body)
    if err:
        return {
            "ok": False,
            "approved": False,
            "decision": "REJECTED",
            "publish": False,
            "manual_review_required": False,
            "reason": err,
            "policy": IMAGE_MODERATION_POLICY,
        }
    scan = _rekognition_scan(image_bytes, float(body.get("minConfidence") or body.get("min_confidence") or 70))
    result = _evaluate_image_scan(scan, float(body.get("textConfidence") or body.get("text_confidence") or 70))
    return {"ok": True, "imageModeration": result}


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

        if p in {"/v1/inqsi/images/moderation-policy", "/v1/images/moderation-policy"} and method == "GET":
            return response(200, IMAGE_MODERATION_POLICY)
        if p in {"/v1/inqsi/images/moderate", "/v1/images/moderate"} and method == "POST":
            result = moderate_member_image({**q, **body})
            return response(200 if result.get("ok", True) else 400, result)

        pull_history = handle_pull_history_route(p, method, q, body)
        if pull_history is not None:
            return response(200 if pull_history.get("ok", True) else 400, pull_history)

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
            return response(200, {"ok": True, "service": "inqsi-backend", "version": "v1", "nonOddsApiRuntime": True, "pullHistoryAlgorithm": True, "imageModeration": True, "architecture": "15_min_pull_history"})
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
