try:
    import mlb_manual_pull as _inqsi_mlb_manual_pull
    import mlb_canonical_pull_patch as _inqsi_mlb_canonical_pull_patch
    _inqsi_mlb_canonical_pull_patch.apply(_inqsi_mlb_manual_pull)
except Exception:
    pass

try:
    import mlb_b10_engine
    import mlb_strength_gate_patch
    mlb_strength_gate_patch.apply(mlb_b10_engine)
except Exception:
    pass

try:
    import mlb_game_winner_engine
    import mlb_accuracy_target_patch
    mlb_accuracy_target_patch.apply(mlb_game_winner_engine)
except Exception:
    pass

try:
    import mlb_game_winner_engine as _mlb_game_winner_engine_for_fundamentals
    import mlb_fundamentals_optimizer_patch
    mlb_fundamentals_optimizer_patch.apply(_mlb_game_winner_engine_for_fundamentals)
except Exception:
    pass

try:
    import mlb_game_winner_engine as _mlb_game_winner_engine_for_final_gate
    import mlb_last_possible_prediction_gate
    mlb_last_possible_prediction_gate.apply(_mlb_game_winner_engine_for_final_gate)
except Exception:
    pass

try:
    import mlb_rolling_24h_audit as _mlb_rolling_24h_audit_for_signal_filter
    import mlb_learning_signal_filter_patch
    mlb_learning_signal_filter_patch.apply(_mlb_rolling_24h_audit_for_signal_filter)
except Exception:
    pass

try:
    import mlb_rolling_24h_audit as _mlb_rolling_24h_audit_for_actionability
    import mlb_audit_actionability_patch
    mlb_audit_actionability_patch.apply(_mlb_rolling_24h_audit_for_actionability)
except Exception:
    pass

try:
    import mlb_rolling_24h_audit as _mlb_rolling_24h_audit_for_locked_card
    import mlb_locked_card_audit_v1
    mlb_locked_card_audit_v1.apply(_mlb_rolling_24h_audit_for_locked_card)
except Exception:
    pass

try:
    import api as _api_for_sportsdataio
    import sportsdataio_api_patch
    sportsdataio_api_patch.apply(_api_for_sportsdataio)
except Exception:
    pass

try:
    import inqsi_api

    def _all_member_image_uploads():
        table = inqsi_api._snapshots_table()
        items = []
        start_key = None
        while True:
            args = {
                "FilterExpression": "record_type = :rt",
                "ExpressionAttributeValues": {":rt": "member_image_upload"},
            }
            if start_key:
                args["ExclusiveStartKey"] = start_key
            result = table.scan(**args)
            items.extend(result.get("Items") or [])
            start_key = result.get("LastEvaluatedKey")
            if not start_key:
                return items

    def _scan_upload_by_id(upload_id):
        for item in _all_member_image_uploads():
            if item.get("upload_id") == upload_id:
                return item
        return None

    def moderation_queue(q):
        status = q.get("status")
        member_id = q.get("member_id") or q.get("memberId")
        try:
            limit = max(1, min(int(q.get("limit") or 50), 200))
        except Exception:
            limit = 50
        items = []
        for item in _all_member_image_uploads():
            if status and item.get("moderation_status") != status:
                continue
            if member_id and str(item.get("member_id")) != str(member_id):
                continue
            items.append(item)
        items = sorted(items, key=lambda row: row.get("created_at", ""))[:limit]
        return {"ok": True, "total_returned": len(items), "limit": limit, "items": [inqsi_api._queue_item_from_upload(item) for item in items]}

    def moderation_dashboard():
        items = _all_member_image_uploads()
        by_status = {}
        by_reason = {}
        visible = 0
        pending = []
        for item in items:
            status = item.get("moderation_status") or "unknown"
            by_status[status] = by_status.get(status, 0) + 1
            reason = item.get("moderation_reason_code") or "unspecified"
            by_reason[reason] = by_reason.get(reason, 0) + 1
            if item.get("is_visible"):
                visible += 1
            if status in {"queued_for_scan", "manual_review"}:
                pending.append(item)
        oldest = sorted(pending, key=lambda row: row.get("created_at", ""))[:10]
        return {
            "ok": True,
            "summary": {
                "total_uploads": len(items),
                "approved": by_status.get("approved", 0),
                "rejected": by_status.get("rejected", 0),
                "manual_review": by_status.get("manual_review", 0),
                "queued_for_scan": by_status.get("queued_for_scan", 0),
                "visible_images": visible,
                "pending_total": len(pending),
            },
            "by_status": by_status,
            "top_reason_codes": [{"reason_code": key, "count": count} for key, count in sorted(by_reason.items(), key=lambda pair: pair[1], reverse=True)[:10]],
            "oldest_pending": [inqsi_api._queue_item_from_upload(item) for item in oldest],
        }

    inqsi_api._scan_upload_by_id = _scan_upload_by_id
    inqsi_api.moderation_queue = moderation_queue
    inqsi_api.moderation_dashboard = moderation_dashboard
except Exception:
    pass

try:
    import frontend_app
    import influencer_portal
    import analytics_events
    import inqsi_api as _inqsi_api_for_routes

    _original_frontend_handler = frontend_app.lambda_handler
    _original_inqsi_handler = _inqsi_api_for_routes.lambda_handler

    def _patched_frontend_handler(event, context):
        analytics_routed = analytics_events.route(event or {})
        if analytics_routed is not None:
            return analytics_routed
        influencer_routed = influencer_portal.route(event or {})
        if influencer_routed is not None:
            return influencer_routed
        return _original_frontend_handler(event, context)

    def _patched_inqsi_handler(event, context):
        analytics_routed = analytics_events.route(event or {})
        if analytics_routed is not None:
            return analytics_routed
        influencer_routed = influencer_portal.route(event or {})
        if influencer_routed is not None:
            return influencer_routed
        return _original_inqsi_handler(event, context)

    frontend_app.lambda_handler = _patched_frontend_handler
    _inqsi_api_for_routes.lambda_handler = _patched_inqsi_handler
except Exception:
    pass
