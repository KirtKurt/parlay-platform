"""Durable quote snapshots for the ARB collector.

Snapshots live in the isolated ARB state table when configured, otherwise in
process memory for tests. This is ingestion, not settlement qualification.
Items stay under the DynamoDB 400KB limit by chunking events.
"""
from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import boto3
except ImportError:
    boto3 = None

CHUNK_EVENTS = 25
_MEMORY: Dict[Tuple[str, str], Dict[str, Any]] = {}


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
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class _MemoryTable:
    def put_item(self, Item: Mapping[str, Any]) -> None:
        _MEMORY[(str(Item["pk"]), str(Item["sk"]))] = dict(Item)

    def get_item(self, Key: Mapping[str, Any], ConsistentRead: bool = False) -> Dict[str, Any]:
        item = _MEMORY.get((str(Key["pk"]), str(Key["sk"])))
        return {"Item": item} if item else {}


def _table():
    name = (os.environ.get("ARB_STATE_TABLE") or "").strip()
    if name and boto3 is not None:
        return boto3.resource("dynamodb").Table(name)
    return _MemoryTable()


def enabled() -> bool:
    return True


def reset_memory() -> None:
    _MEMORY.clear()


def _pk(sport: str) -> str:
    return f"QUOTE#{str(sport or '').strip().lower()}"


def put_snapshot(
    sport: str,
    events: List[Mapping[str, Any]],
    *,
    meta: Optional[Mapping[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    table = _table()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    rows = list(events or [])
    chunks = [rows[i:i + CHUNK_EVENTS] for i in range(0, len(rows), CHUNK_EVENTS)] or [[]]
    for index, chunk in enumerate(chunks):
        table.put_item(Item={
            "pk": _pk(sport),
            "sk": f"CHUNK#{index:03d}",
            "updated_at_ms": now,
            "data": _ddb({"events": chunk, "index": index}),
        })
    head = {
        "sport": str(sport).strip().lower(),
        "fetched_at_ms": now,
        "n_events": len(rows),
        "n_chunks": len(chunks),
        "ok": bool((meta or {}).get("ok", True)),
        "regions": (meta or {}).get("regions"),
        "markets": (meta or {}).get("markets") or (meta or {}).get("featured"),
        "error": (meta or {}).get("error"),
    }
    table.put_item(Item={
        "pk": _pk(sport),
        "sk": "HEAD",
        "updated_at_ms": now,
        "data": _ddb(head),
    })
    return head


def get_head(sport: str) -> Optional[Dict[str, Any]]:
    item = _table().get_item(Key={"pk": _pk(sport), "sk": "HEAD"}, ConsistentRead=True).get("Item")
    return _plain(item.get("data")) if item else None


def get_snapshot(sport: str, *, max_age_seconds: int = 120) -> Optional[Dict[str, Any]]:
    head = get_head(sport)
    if not head:
        return None
    age_ms = int(time.time() * 1000) - int(head.get("fetched_at_ms") or 0)
    if age_ms > max(1, int(max_age_seconds)) * 1000:
        return {"ok": False, "stale": True, "age_ms": age_ms, "head": head, "events": []}
    events: List[Dict[str, Any]] = []
    table = _table()
    for index in range(int(head.get("n_chunks") or 0)):
        item = table.get_item(Key={"pk": _pk(sport), "sk": f"CHUNK#{index:03d}"}, ConsistentRead=True).get("Item")
        if not item:
            return {"ok": False, "incomplete": True, "head": head, "events": []}
        events.extend(_plain(item.get("data") or {}).get("events") or [])
    return {"ok": True, "stale": False, "head": head, "events": events, "age_ms": age_ms}


def put_checkpoint(payload: Mapping[str, Any], *, now_ms: Optional[int] = None) -> Dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    data = {**dict(payload), "updated_at_ms": now}
    _table().put_item(Item={"pk": "QUOTE#META", "sk": "CHECKPOINT", "updated_at_ms": now, "data": _ddb(data)})
    return data


def get_checkpoint() -> Dict[str, Any]:
    item = _table().get_item(Key={"pk": "QUOTE#META", "sk": "CHECKPOINT"}, ConsistentRead=True).get("Item")
    return _plain(item.get("data") or {}) if item else {}
