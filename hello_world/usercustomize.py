"""Runtime routing guard for live market endpoints.

Python imports usercustomize after sitecustomize. This wrapper catches the
live odds and latest market-board endpoints before the older route chain can
return a generic 404.
"""

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
    _inqsi_mlb_slate_prediction_lock.apply(_inqsi_mlb_game_winner_for_slate_lock)
    if hasattr(_inqsi_mlb_game_winner_for_slate_lock, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED"):
        delattr(_inqsi_mlb_game_winner_for_slate_lock, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED")
    _inqsi_mlb_last_possible_prediction_gate.apply(_inqsi_mlb_game_winner_for_slate_lock)
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

    def _route_live_market(event):
        event = event or {}
        path = _path(event)
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
