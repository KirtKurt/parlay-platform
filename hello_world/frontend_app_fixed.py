import json
from decimal import Decimal
from typing import Any, Dict

import boto3
import frontend_app


dynamodb = boto3.resource("dynamodb")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return str(value)


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,OPTIONS"},
        "body": json.dumps(body, default=_json_default),
    }


def _body(event):
    try:
        return json.loads(event.get("body") or "{}")
    except Exception:
        return {}


def _table():
    import os
    return dynamodb.Table(os.environ["SNAPSHOTS_TABLE"])


def _public(item):
    return {k: v for k, v in item.items() if k not in {"PK", "SK", "record_type"}}


def _find_upload(upload_id):
    table = _table()
    start_key = None
    while True:
        args = {
            "FilterExpression": "record_type = :rt AND upload_id = :uid",
            "ExpressionAttributeValues": {":rt": "member_image_upload", ":uid": upload_id},
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        result = table.scan(**args)
        items = result.get("Items") or []
        if items:
            return items[0]
        start_key = result.get("LastEvaluatedKey")
        if not start_key:
            return None


def _review(event, upload_id):
    body = _body(event)
    decision = str(body.get("decision") or "").lower()
    if decision not in {"approved", "rejected"}:
        return _resp(400, {"ok": False, "error": "decision must be approved or rejected"})
    item = _find_upload(upload_id)
    if not item:
        return _resp(400, {"ok": False, "error": "upload_id not found"})
    import time
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    item["moderation_status"] = decision
    item["moderation_reason_code"] = body.get("reason_code") or ("approved_by_reviewer" if decision == "approved" else "rejected_by_reviewer")
    item["reviewed_at"] = now
    item["updated_at"] = now
    item["published_at"] = now if decision == "approved" else ""
    item["is_visible"] = decision == "approved"
    item["reviewer_decision_json"] = {"reviewer_id": body.get("reviewer_id") or "admin", "decision": decision, "reason_code": item["moderation_reason_code"], "reviewed_at": now}
    _table().put_item(Item=item)
    return _resp(200, {"ok": True, "upload": _public(item)})


def lambda_handler(event, context):
    event = event or {}
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    method = (event.get("httpMethod") or "GET").upper()
    if method == "POST" and path.startswith("/v1/inqsi/moderation/review/"):
        return _review(event, path.rsplit("/", 1)[-1])
    return frontend_app.lambda_handler(event, context)
