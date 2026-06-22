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

    _original_frontend_handler = frontend_app.lambda_handler

    def _patched_frontend_handler(event, context):
        routed = influencer_portal.route(event or {})
        if routed is not None:
            return routed
        return _original_frontend_handler(event, context)

    frontend_app.lambda_handler = _patched_frontend_handler
except Exception:
    pass
