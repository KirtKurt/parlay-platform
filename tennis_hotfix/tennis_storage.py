from __future__ import annotations

import copy
import hashlib
import json
import zlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from tennis_config import Settings
from tennis_json import ddb_safe, json_safe


class ConditionalWriteConflict(RuntimeError):
    pass


class BaseStore:
    def put_item(self, role: str, item: Dict[str, Any], *, if_absent: bool = False) -> Dict[str, Any]:
        raise NotImplementedError

    def get_item(self, role: str, pk: str, sk: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def query_items(self, role: str, pk: str, *, limit: int = 500, ascending: bool = True) -> List[Dict[str, Any]]:
        raise NotImplementedError


@dataclass
class DynamoStore(BaseStore):
    settings: Settings

    def __post_init__(self) -> None:
        import boto3

        self._ddb = boto3.resource("dynamodb")
        self._tables: Dict[str, Any] = {}

    def _table_name(self, role: str) -> str:
        mapping = {
            "snapshots": self.settings.snapshots_table,
            "signals": self.settings.signal_ledger_table,
            "predictions": self.settings.predictions_table,
            "outcomes": self.settings.outcomes_table,
            "models": self.settings.models_table,
        }
        name = mapping.get(role, "")
        if not name:
            raise RuntimeError(f"Tennis {role} table is not configured")
        return name

    def _table(self, role: str):
        name = self._table_name(role)
        if name not in self._tables:
            self._tables[name] = self._ddb.Table(name)
        return self._tables[name]

    _SNAPSHOT_PACK_VERSION = "TENNIS-DDB-ZLIB-JSON-v1"
    _SNAPSHOT_PACK_THRESHOLD_BYTES = 280_000
    _SNAPSHOT_INLINE_LIMIT_BYTES = 300_000
    _SNAPSHOT_CHUNK_BYTES = 280_000

    @staticmethod
    def _binary_bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, memoryview):
            return value.tobytes()
        raw = getattr(value, "value", None)
        if isinstance(raw, bytes):
            return raw
        raise RuntimeError("Tennis packed snapshot contains an invalid binary payload")

    @classmethod
    def _encode_snapshot(cls, item: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[bytes]]:
        logical = json_safe(item)
        payload = json.dumps(logical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(payload) < cls._SNAPSHOT_PACK_THRESHOLD_BYTES:
            return ddb_safe(item), None
        compressed = zlib.compress(payload, level=9)
        digest = hashlib.sha256(compressed).hexdigest()
        packed: Dict[str, Any] = {
            "PK": str(item["PK"]),
            "SK": str(item["SK"]),
            "record_type": str(item.get("record_type") or ""),
            "sport": str(item.get("sport") or "tennis"),
            "slate_date": str(item.get("slate_date") or ""),
            "pulled_at": str(item.get("pulled_at") or ""),
            "pull_id": str(item.get("pull_id") or ""),
            "payload_fingerprint": str(item.get("payload_fingerprint") or ""),
            "__tennis_pack_version": cls._SNAPSHOT_PACK_VERSION,
            "__tennis_pack_codec": "zlib+json",
            "__tennis_pack_original_bytes": len(payload),
            "__tennis_pack_compressed_bytes": len(compressed),
            "__tennis_pack_sha256": digest,
        }
        if len(compressed) <= cls._SNAPSHOT_INLINE_LIMIT_BYTES:
            packed["__tennis_pack_payload"] = compressed
            return packed, None
        return packed, compressed

    def _write_snapshot_chunks(self, compressed: bytes, digest: str) -> Tuple[str, int]:
        chunk_pk = f"PACKED#tennis#{digest}"
        table = self._table("snapshots")
        count = 0
        for offset in range(0, len(compressed), self._SNAPSHOT_CHUNK_BYTES):
            chunk = compressed[offset : offset + self._SNAPSHOT_CHUNK_BYTES]
            table.put_item(Item={
                "PK": chunk_pk,
                "SK": f"CHUNK#{count:05d}",
                "record_type": "tennis_packed_snapshot_chunk",
                "sport": "tennis",
                "data": chunk,
            })
            count += 1
        return chunk_pk, count

    def _restore_snapshot_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if item.get("__tennis_pack_version") != self._SNAPSHOT_PACK_VERSION:
            return json_safe(item)
        compressed_value = item.get("__tennis_pack_payload")
        if compressed_value is not None:
            compressed = self._binary_bytes(compressed_value)
        else:
            chunk_pk = str(item.get("__tennis_pack_chunk_pk") or "")
            chunk_count = int(item.get("__tennis_pack_chunk_count") or 0)
            if not chunk_pk or chunk_count <= 0:
                raise RuntimeError("Tennis packed snapshot is missing chunk metadata")
            from boto3.dynamodb.conditions import Key
            rows: List[Dict[str, Any]] = []
            exclusive_start_key = None
            while len(rows) < chunk_count:
                kwargs: Dict[str, Any] = {
                    "KeyConditionExpression": Key("PK").eq(chunk_pk),
                    "ScanIndexForward": True,
                }
                if exclusive_start_key:
                    kwargs["ExclusiveStartKey"] = exclusive_start_key
                response = self._table("snapshots").query(**kwargs)
                rows.extend(response.get("Items") or [])
                exclusive_start_key = response.get("LastEvaluatedKey")
                if not exclusive_start_key:
                    break
            rows.sort(key=lambda row: str(row.get("SK") or ""))
            if len(rows) != chunk_count:
                raise RuntimeError(f"Tennis packed snapshot chunk set incomplete: expected={chunk_count} actual={len(rows)}")
            compressed = b"".join(self._binary_bytes(row.get("data")) for row in rows)
        digest = hashlib.sha256(compressed).hexdigest()
        expected_digest = str(item.get("__tennis_pack_sha256") or "")
        if expected_digest and digest != expected_digest:
            raise RuntimeError("Tennis packed snapshot checksum mismatch")
        logical = json.loads(zlib.decompress(compressed).decode("utf-8"))
        if not isinstance(logical, dict):
            raise RuntimeError("Tennis packed snapshot did not decode to an object")
        logical["PK"] = str(item.get("PK") or logical.get("PK") or "")
        logical["SK"] = str(item.get("SK") or logical.get("SK") or "")
        return json_safe(logical)

    def put_item(self, role: str, item: Dict[str, Any], *, if_absent: bool = False) -> Dict[str, Any]:
        from botocore.exceptions import ClientError

        table = self._table(role)
        if role == "snapshots":
            stored_item, packed_chunks = self._encode_snapshot(item)
            if packed_chunks is not None:
                digest = str(stored_item["__tennis_pack_sha256"])
                chunk_pk, chunk_count = self._write_snapshot_chunks(packed_chunks, digest)
                stored_item["__tennis_pack_chunk_pk"] = chunk_pk
                stored_item["__tennis_pack_chunk_count"] = chunk_count
        else:
            stored_item = ddb_safe(item)
        kwargs: Dict[str, Any] = {"Item": stored_item}
        if if_absent:
            kwargs["ConditionExpression"] = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
        try:
            table.put_item(**kwargs)
            return {"ok": True, "created": True, "item": json_safe(item)}
        except ClientError as exc:
            if if_absent and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                existing = self.get_item(role, str(item["PK"]), str(item["SK"]))
                return {"ok": True, "created": False, "item": existing}
            raise

    def get_item(self, role: str, pk: str, sk: str) -> Optional[Dict[str, Any]]:
        response = self._table(role).get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True)
        item = response.get("Item")
        if not item:
            return None
        return self._restore_snapshot_item(item) if role == "snapshots" else json_safe(item)

    def query_items(self, role: str, pk: str, *, limit: int = 500, ascending: bool = True) -> List[Dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        requested = max(1, min(int(limit), 10000))
        items: List[Dict[str, Any]] = []
        exclusive_start_key = None
        while len(items) < requested:
            kwargs: Dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(pk),
                "Limit": min(requested - len(items), 1000),
                "ScanIndexForward": ascending,
            }
            if exclusive_start_key:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            response = self._table(role).query(**kwargs)
            for raw_item in response.get("Items") or []:
                items.append(self._restore_snapshot_item(raw_item) if role == "snapshots" else json_safe(raw_item))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                break
        return items[:requested]


class MemoryStore(BaseStore):
    def __init__(self) -> None:
        self._data: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def put_item(self, role: str, item: Dict[str, Any], *, if_absent: bool = False) -> Dict[str, Any]:
        key = (role, str(item["PK"]), str(item["SK"]))
        if if_absent and key in self._data:
            return {"ok": True, "created": False, "item": copy.deepcopy(self._data[key])}
        self._data[key] = copy.deepcopy(json_safe(item))
        return {"ok": True, "created": True, "item": copy.deepcopy(self._data[key])}

    def get_item(self, role: str, pk: str, sk: str) -> Optional[Dict[str, Any]]:
        item = self._data.get((role, pk, sk))
        return copy.deepcopy(item) if item is not None else None

    def query_items(self, role: str, pk: str, *, limit: int = 500, ascending: bool = True) -> List[Dict[str, Any]]:
        rows = [
            copy.deepcopy(item)
            for (item_role, item_pk, _), item in self._data.items()
            if item_role == role and item_pk == pk
        ]
        rows.sort(key=lambda row: str(row.get("SK") or ""), reverse=not ascending)
        return rows[:limit]

    def dump(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = []
        for (item_role, _, _), item in self._data.items():
            if role is None or role == item_role:
                rows.append(copy.deepcopy(item))
        return rows
