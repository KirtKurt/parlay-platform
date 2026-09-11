"""Persistent lifecycle state for Inqsi ARB user-reviewed opportunities.

This store records analysis state only. It does not place transactions.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


def _table():
    name = os.environ.get("ARB_STATE_TABLE", "").strip()
    if not name:
        raise RuntimeError("ARB_STATE_TABLE not configured")
    return boto3.resource("dynamodb").Table(name)


def _ddb(v: Any) -> Any:
    if isinstance(v, float): return Decimal(str(v))
    if isinstance(v, dict): return {str(k): _ddb(x) for k, x in v.items()}
    if isinstance(v, list): return [_ddb(x) for x in v]
    return v


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, dict): return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list): return [_plain(x) for x in v]
    return v


def put(user_id: str, position_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    item = {
        "pk": f"USER#{user_id}",
        "sk": f"POSITION#{position_id}",
        "entity": "POSITION",
        "position_id": position_id,
        "updated_at_ms": int(time.time() * 1000),
        "data": _ddb(data),
    }
    _table().put_item(Item=item)
    return data


def get(user_id: str, position_id: str) -> Optional[Dict[str, Any]]:
    item = _table().get_item(Key={"pk": f"USER#{user_id}", "sk": f"POSITION#{position_id}"}, ConsistentRead=True).get("Item")
    return _plain(item.get("data")) if item else None


def list_for_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    result = _table().query(
        KeyConditionExpression=Key("pk").eq(f"USER#{user_id}") & Key("sk").begins_with("POSITION#"),
        Limit=max(1, min(int(limit), 100)),
        ScanIndexForward=False,
    )
    return [_plain(x.get("data")) for x in result.get("Items", [])]
