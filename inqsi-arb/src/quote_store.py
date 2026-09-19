"""Durable quote snapshots for the ARB collector.

Snapshots live in the isolated ARB state table when configured, otherwise in
process memory for tests. This is ingestion, not settlement qualification.
Items stay under the DynamoDB 400KB limit by chunking events.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import boto3
except ImportError:
    boto3 = None

CHUNK_EVENTS = 25
MAX_ITEM_BYTES = 350_000
_MEMORY: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _snapshot_ttl(now_ms: int) -> int:
    try:
        retention = max(600, int(os.environ.get("ARB_SNAPSHOT_TTL_SECONDS", "86400")))
    except ValueError:
        retention = 86400
    return now_ms // 1000 + retention


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


def _item_size(item: Mapping[str, Any]) -> int:
    return len(json.dumps(item, default=str, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _chunks_for(sport: str, rows: List[Mapping[str, Any]], version: str, now: int) -> List[List[Mapping[str, Any]]]:
    chunks: List[List[Mapping[str, Any]]] = []
    current: List[Mapping[str, Any]] = []
    for event in rows:
        tentative = current + [event]
        item = {
            "pk": _pk(sport), "sk": f"CHUNK#{version}#{len(chunks):03d}",
            "updated_at_ms": now, "data": {"events": tentative, "index": len(chunks)},
        }
        if _item_size(item) <= MAX_ITEM_BYTES and len(tentative) <= CHUNK_EVENTS:
            current = tentative
            continue
        if not current:
            raise ValueError("snapshot event exceeds DynamoDB item safety budget")
        chunks.append(current)
        current = [event]
        single = {
            "pk": _pk(sport), "sk": f"CHUNK#{version}#{len(chunks):03d}",
            "updated_at_ms": now, "data": {"events": current, "index": len(chunks)},
        }
        if _item_size(single) > MAX_ITEM_BYTES:
            raise ValueError("snapshot event exceeds DynamoDB item safety budget")
    if current or not chunks:
        chunks.append(current)
    return chunks


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
    version = f"{now}-{uuid.uuid4().hex[:12]}"
    chunks = _chunks_for(sport, rows, version, now)
    ttl = _snapshot_ttl(now)
    for index, chunk in enumerate(chunks):
        table.put_item(Item={
            "pk": _pk(sport),
            "sk": f"CHUNK#{version}#{index:03d}",
            "updated_at_ms": now,
            "ttl": ttl,
            "data": _ddb({"events": chunk, "index": index}),
        })
    head = {
        "sport": str(sport).strip().lower(),
        "fetched_at_ms": now,
        "n_events": len(rows),
        "n_chunks": len(chunks),
        "version": version,
        "ok": bool((meta or {}).get("ok", True)),
        "regions": (meta or {}).get("regions"),
        "markets": (meta or {}).get("markets") or (meta or {}).get("featured"),
        "error": (meta or {}).get("error"),
    }
    table.put_item(Item={
        "pk": _pk(sport),
        "sk": "HEAD",
        "updated_at_ms": now,
        "ttl": ttl,
        "data": _ddb(head),
    })
    return head


def get_head(sport: str) -> Optional[Dict[str, Any]]:
    item = _table().get_item(Key={"pk": _pk(sport), "sk": "HEAD"}, ConsistentRead=True).get("Item")
    return _plain(item.get("data")) if item else None


def list_snapshot_sports() -> List[str]:
    table = _table()
    if isinstance(table, _MemoryTable):
        return sorted({pk.split("#", 1)[1] for pk, sk in _MEMORY if sk == "HEAD" and pk != "QUOTE#META"})
    rows: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        "ProjectionExpression": "pk, sk",
        "FilterExpression": "sk = :head",
        "ExpressionAttributeValues": {":head": "HEAD"},
    }
    while True:
        page = table.scan(**kwargs)
        rows.extend(page.get("Items") or [])
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return sorted({str(row.get("pk") or "").split("#", 1)[1] for row in rows if str(row.get("pk") or "").startswith("QUOTE#") and row.get("pk") != "QUOTE#META"})


def get_snapshot(sport: str, *, max_age_seconds: int = 120) -> Optional[Dict[str, Any]]:
    head = get_head(sport)
    if not head:
        return None
    age_ms = int(time.time() * 1000) - int(head.get("fetched_at_ms") or 0)
    if not head.get("ok"):
        return {"ok": False, "stale": False, "incomplete": True, "age_ms": age_ms, "head": head, "events": []}
    if age_ms > max(1, int(max_age_seconds)) * 1000:
        return {"ok": False, "stale": True, "age_ms": age_ms, "head": head, "events": []}
    events: List[Dict[str, Any]] = []
    table = _table()
    version = str(head.get("version") or "")
    for index in range(int(head.get("n_chunks") or 0)):
        sk = f"CHUNK#{version}#{index:03d}" if version else f"CHUNK#{index:03d}"
        item = table.get_item(Key={"pk": _pk(sport), "sk": sk}, ConsistentRead=True).get("Item")
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
