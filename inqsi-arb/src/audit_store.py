"""Durable audit storage for Inqsi ARB scan and lifecycle evidence."""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    import boto3
    from boto3.dynamodb.conditions import Attr, Key
except ImportError:  # local tests without AWS runtime
    boto3 = None
    Attr = Key = None


def _table():
    name = os.environ.get("ARB_AUDIT_TABLE", "").strip()
    if not name or boto3 is None:
        return None
    return boto3.resource("dynamodb").Table(name)


def _ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {str(k): _ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ddb(v) for v in value]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def enabled() -> bool:
    return _table() is not None


def record(kind: str, payload: Dict[str, Any]) -> Optional[str]:
    table = _table()
    if table is None:
        return None
    now_ms = int(time.time() * 1000)
    event_id = f"{now_ms:013d}#{uuid.uuid4().hex}"
    table.put_item(Item={
        "pk": "AUDIT",
        "sk": event_id,
        "kind": str(kind).strip().upper() or "UNKNOWN",
        "created_at_ms": now_ms,
        "payload": _ddb(payload),
    }, ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)")
    return event_id


def recent(kind: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Return newest audit events without mutating state.

    The table key is append-only and time-sortable. Filtering by kind remains
    fail-safe: pagination continues until the requested number of matching rows
    is collected or the query is exhausted.
    """
    table = _table()
    if table is None or Key is None:
        return []
    wanted = max(1, min(int(limit), 100))
    kind_norm = str(kind or "").strip().upper()
    out: List[Dict[str, Any]] = []
    exclusive_start_key = None
    pages = 0
    while len(out) < wanted and pages < 10:
        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq("AUDIT"),
            "ScanIndexForward": False,
            "Limit": min(100, max(wanted * 2, 25)),
        }
        if kind_norm and Attr is not None:
            kwargs["FilterExpression"] = Attr("kind").eq(kind_norm)
        if exclusive_start_key:
            kwargs["ExclusiveStartKey"] = exclusive_start_key
        result = table.query(**kwargs)
        for item in result.get("Items", []):
            out.append({
                "event_id": str(item.get("sk") or ""),
                "kind": str(item.get("kind") or ""),
                "created_at_ms": int(item.get("created_at_ms") or 0),
                "payload": _plain(item.get("payload") or {}),
            })
            if len(out) >= wanted:
                break
        exclusive_start_key = result.get("LastEvaluatedKey")
        pages += 1
        if not exclusive_start_key:
            break
    return out[:wanted]


def claim_once(namespace: str, identity: str, *, ttl_seconds: int = 21600, claim_token: str | None = None) -> bool:
    """Atomically claim an alert identity.

    Returns True only for the first claimant during the TTL window. Expired
    claims may be reclaimed even before DynamoDB asynchronous TTL deletion.
    An optional token permits safe release after a failed downstream publish.
    """
    table = _table()
    if table is None:
        return False
    now = int(time.time())
    pk = "CLAIM#" + (str(namespace).strip().upper() or "DEFAULT")
    sk = str(identity).strip()
    if not sk:
        return False
    item = {
        "pk": pk,
        "sk": sk,
        "kind": "CLAIM",
        "created_at_ms": now * 1000,
        "ttl": now + max(60, int(ttl_seconds)),
    }
    if claim_token:
        item["claim_token"] = str(claim_token)
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(pk) OR #ttl < :now",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":now": now},
        )
        return True
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def release_claim(namespace: str, identity: str, *, claim_token: str) -> bool:
    """Expire only a claim owned by this caller after a failed notification.

    Uses the existing PutItem permission rather than requiring DeleteItem.
    A token-matched conditional write cannot release a newer caller\'s claim.
    """
    table = _table()
    token = str(claim_token or "").strip()
    sk = str(identity).strip()
    if table is None or not token or not sk:
        return False
    now = int(time.time())
    try:
        table.put_item(
            Item={
                "pk": "CLAIM#" + (str(namespace).strip().upper() or "DEFAULT"),
                "sk": sk,
                "kind": "CLAIM_RELEASED",
                "created_at_ms": now * 1000,
                "ttl": now - 1,
                "claim_token": token,
            },
            ConditionExpression="claim_token = :token",
            ExpressionAttributeValues={":token": token},
        )
        return True
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        code = str((response.get("Error") or {}).get("Code") or "")
        if code == "ConditionalCheckFailedException":
            return False
        raise
