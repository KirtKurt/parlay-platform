"""Runtime routing guard for live market endpoints.

Python imports usercustomize after sitecustomize. This wrapper catches selected
routes before the older route chain can return generic 404/502 responses.
"""

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return str(value)


def _json_resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _today_et():
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


try:
    import inqsi_pull_history as _inqsi_pull_history
    import slate_date_patch as _inqsi_slate_date_patch
    import signal_score_guard as _inqsi_signal_score_guard
    import pull_dedupe_guard as _inqsi_pull_dedupe_guard
    _inqsi_slate_date_patch.apply_to_history(_inqsi_pull_history)
    _inqsi_signal_score_guard.apply(_inqsi_pull_history)
    _inqsi_pull_dedupe_guard.apply(_inqsi_pull_history)
except Exception:
    pass

try:
    import mlb_b10_engine as _inqsi_mlb_b10_engine
    import mlb_full_signal_board_patch as _inqsi_mlb_full_signal_board_patch
    _inqsi_mlb_full_signal_board_patch.apply(_inqsi_mlb_b10_engine)
except Exception:
    pass

try:
    import mlb_game_winner_engine as _inqsi_mlb_game_winner_for_slate_lock
    import mlb_slate_prediction_lock as _inqsi_mlb_slate_prediction_lock
    import mlb_last_possible_prediction_gate as _inqsi_mlb_last_possible_prediction_gate
    import mlb_balanced_signal_gate as _inqsi_mlb_balanced_signal_gate
    import mlb_ml_signal_layers as _inqsi_mlb_ml_signal_layers
    import mlb_signal_policy_v12 as _inqsi_mlb_signal_policy_v12
    import mlb_directional_score_v1 as _inqsi_mlb_directional_score_v1
    import mlb_ml_runtime_overlay as _inqsi_mlb_ml_runtime_overlay
    _inqsi_mlb_slate_prediction_lock.apply(_inqsi_mlb_game_winner_for_slate_lock)
    for attr, patch in [
        ("_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED", _inqsi_mlb_last_possible_prediction_gate),
        ("_INQSI_MLB_BALANCED_SIGNAL_GATE_APPLIED", _inqsi_mlb_balanced_signal_gate),
        ("_INQSI_MLB_ML_SIGNAL_LAYERS_APPLIED", _inqsi_mlb_ml_signal_layers),
        ("_INQSI_MLB_SIGNAL_POLICY_V12_APPLIED", _inqsi_mlb_signal_policy_v12),
        ("_INQSI_MLB_DIRECTIONAL_SCORE_V1_APPLIED", _inqsi_mlb_directional_score_v1),
        ("_INQSI_MLB_ML_RUNTIME_OVERLAY_APPLIED", _inqsi_mlb_ml_runtime_overlay),
    ]:
        if hasattr(_inqsi_mlb_game_winner_for_slate_lock, attr):
            delattr(_inqsi_mlb_game_winner_for_slate_lock, attr)
        patch.apply(_inqsi_mlb_game_winner_for_slate_lock)
except Exception:
    pass

try:
    import frontend_app
    import inqsi_api

    try:
        import admin_auth
    except Exception:
        admin_auth = None

    try:
        import odds_live_ingestion
    except Exception:
        odds_live_ingestion = None

    try:
        import slate_date_patch
        slate_date_patch.apply_to_odds(odds_live_ingestion)
    except Exception:
        pass

    try:
        import sport_key_patch
        sport_key_patch.apply(odds_live_ingestion)
    except Exception:
        pass

    try:
        import pull_report_guard
        pull_report_guard.apply(odds_live_ingestion)
    except Exception:
        pass

    try:
        import market_board
    except Exception:
        market_board = None

    _next_frontend_handler = frontend_app.lambda_handler
    _next_inqsi_handler = inqsi_api.lambda_handler

    def _path(event):
        return ((event or {}).get("rawPath") or (event or {}).get("path") or "/").rstrip("/") or "/"

    def _query(event):
        return (event or {}).get("queryStringParameters") or {}

    def _route_mlb_core(event):
        event = event or {}
        path = _path(event)
        params = _query(event)
        if not path.startswith("/v1/mlb"):
            return None
        date = params.get("game_date_et") or params.get("date") or _today_et()
        try:
            import mlb_game_winner_engine as engine
            engine_ok = True
            engine_error = None
        except Exception as exc:
            engine = None
            engine_ok = False
            engine_error = str(exc)

        if path == "/v1/mlb/model/version":
            return _json_resp(200, {
                "ok": True,
                "sport": "mlb",
                "model_version": "INQSI-MLB-v2.1-core-proxy-smoke-safe",
                "game_winner_model": getattr(engine, "MODEL_VERSION", None) if engine is not None else None,
                "game_winner_engine": getattr(engine, "ENGINE", None) if engine is not None else None,
                "engine_import_ok": engine_ok,
                "engine_import_error": engine_error,
                "pick_type": "individual_game_moneyline",
                "parlaysEnabled": False,
                "sourcePolicy": "The Odds API stored pull history only for production picks.",
            })

        if path in {"/v1/mlb/today", "/v1/mlb/games", "/v1/mlb/predictions", "/v1/mlb/game-winners"}:
            if engine is None:
                return _json_resp(200, {"ok": False, "sport": "mlb", "date": date, "error": engine_error, "winner_predictions": [], "count": 0})
            try:
                payload = engine.predict_all(date, store=str(params.get("store", "false")).lower() == "true", limit=min(int(params.get("limit") or 500), 500))
            except Exception as exc:
                return _json_resp(200, {"ok": False, "sport": "mlb", "date": date, "error": str(exc), "winner_predictions": [], "count": 0})
            if path == "/v1/mlb/today":
                return _json_resp(200, {
                    "ok": True,
                    "sport": "mlb",
                    "date": date,
                    "model_version": "INQSI-MLB-v2.1-core-proxy-smoke-safe",
                    "game_winner_model": payload.get("modelVersion"),
                    "count": payload.get("count", 0),
                    "promotedCount": payload.get("promotedCount", 0),
                    "pullCount": payload.get("pullCount"),
                    "latestPullAt": payload.get("latestPullAt"),
                    "priority": "individual_game_moneyline_picks",
                    "parlaysEnabled": False,
                })
            return _json_resp(200, {**payload, "winner_predictions": payload.get("predictions") or [], "parlaysEnabled": False})

        return None

    def _route_live_market(event):
        event = event or {}
        path = _path(event)
        mlb_routed = _route_mlb_core(event)
        if mlb_routed is not None:
            return mlb_routed
        if (path.startswith("/v1/inqsi/odds") or path.startswith("/v1/odds")) and odds_live_ingestion is not None:
            if admin_auth is not None:
                auth = admin_auth.check(event)
                if auth is not None:
                    return auth
            routed = odds_live_ingestion.route(event)
            if routed is not None:
                return routed
        if (path.startswith("/v1/inqsi/markets") or path.startswith("/v1/markets")) and market_board is not None:
            routed = market_board.route(event)
            if routed is not None:
                return routed
        return None

    def _frontend_handler(event, context):
        routed = _route_live_market(event or {})
        if routed is not None:
            return routed
        return _next_frontend_handler(event, context)

    def _inqsi_handler(event, context):
        routed = _route_live_market(event or {})
        if routed is not None:
            return routed
        return _next_inqsi_handler(event, context)

    frontend_app.lambda_handler = _frontend_handler
    inqsi_api.lambda_handler = _inqsi_handler
except Exception:
    pass

try:
    import odds_live_ingestion as _inqsi_odds_for_tennis
    import slate_date_patch as _inqsi_slate_patch_for_odds
    import sport_key_patch as _inqsi_sport_key_patch
    import pull_report_guard as _inqsi_pull_report_guard
    _inqsi_slate_patch_for_odds.apply_to_odds(_inqsi_odds_for_tennis)
    _inqsi_sport_key_patch.apply(_inqsi_odds_for_tennis)
    _inqsi_pull_report_guard.apply(_inqsi_odds_for_tennis)
except Exception:
    pass
