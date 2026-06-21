import json
import os
from decimal import Decimal
from typing import Any, Dict

import boto3
from boto3.dynamodb.conditions import Key

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type,authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}

DDB = boto3.resource("dynamodb")
LIVE_STATUS = "live_paid"


def safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items()}
    return value


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": CORS_HEADERS, "body": json.dumps(safe(body))}


def table_count(env_name: str) -> int:
    table_name = os.environ.get(env_name)
    if not table_name:
        return 0
    return int(DDB.Table(table_name).scan(Select="COUNT", Limit=1000).get("Count", 0))


def live_member_count() -> int:
    table_name = os.environ.get("MEMBERSHIP_TABLE")
    if not table_name:
        return 0
    table = DDB.Table(table_name)
    # A full production report should aggregate by creator daily. This count is a safe live operator snapshot.
    result = table.scan(FilterExpression=Key("member_status").eq(LIVE_STATUS), Select="COUNT", Limit=1000)
    return int(result.get("Count", 0))


def handle_http(event: Dict[str, Any]) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", event.get("httpMethod", "GET")).upper()
    if method == "OPTIONS":
        return response(200, {"ok": True})
    return response(200, {
        "product": "InQsi",
        "summary": {
            "creators": table_count("CREATORS_TABLE"),
            "attributionEvents": table_count("ATTRIBUTION_EVENTS_TABLE"),
            "linkedMembers": table_count("USER_ATTRIBUTION_TABLE"),
            "liveMembers": live_member_count(),
            "storedGames": table_count("GAMES_TABLE"),
            "storedSnapshots": table_count("SNAPSHOTS_TABLE"),
            "statusRows": table_count("GAME_STATUS_TABLE")
        },
        "status": "ready"
    })


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    return handle_http(event or {})
