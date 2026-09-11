"""Durable audit storage for Inqsi ARB scan evidence."""
from __future__ import annotations

import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    import boto3
except ImportError:  # local tests
    boto3 = None


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
        "kind": str(kind),
        "created_at_ms": now_ms,
        "payload": _ddb(payload),
    }, ConditionExpression="attribute_not_exists(pk) AND attribute_not_exists(sk)")
    return event_id
