"""WebSocket connection registry and broadcast helper for Inqsi ARB."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError


def _connections():
    return boto3.resource("dynamodb").Table(os.environ["ARB_CONNECTIONS_TABLE"])


def handler(event: Dict[str, Any], context: Any):
    ctx = event.get("requestContext") or {}
    route = ctx.get("routeKey")
    connection_id = ctx.get("connectionId")
    if route == "$connect":
        _connections().put_item(Item={
            "connection_id": connection_id,
            "connected_at": int(time.time()),
            "ttl": int(time.time()) + 86400,
        })
        return {"statusCode": 200}
    if route == "$disconnect":
        _connections().delete_item(Key={"connection_id": connection_id})
        return {"statusCode": 200}
    if route == "ping":
        return {"statusCode": 200, "body": "pong"}
    return {"statusCode": 200}


def broadcast(payload: Dict[str, Any]) -> Dict[str, int]:
    endpoint = os.environ.get("ARB_WEBSOCKET_MANAGEMENT_ENDPOINT", "").strip()
    if not endpoint:
        return {"sent": 0, "stale": 0}
    table = _connections()
    api = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint)
    rows = table.scan(ProjectionExpression="connection_id").get("Items", [])
    sent = stale = 0
    data = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    for row in rows:
        cid = row.get("connection_id")
        if not cid:
            continue
        try:
            api.post_to_connection(ConnectionId=cid, Data=data)
            sent += 1
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 410:
                table.delete_item(Key={"connection_id": cid})
                stale += 1
            else:
                raise
    return {"sent": sent, "stale": stale}
