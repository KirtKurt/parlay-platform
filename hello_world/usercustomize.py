"""Runtime routing guard for live market endpoints.

Python imports usercustomize after sitecustomize. This wrapper catches the
live odds and latest market-board endpoints before the older route chain can
return a generic 404.
"""

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
        import sport_key_patch
        sport_key_patch.apply(odds_live_ingestion)
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
