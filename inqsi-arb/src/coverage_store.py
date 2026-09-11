"""Persistent multidimensional coverage registry for Inqsi ARB.

Coverage is evidence, not a claim of availability. Each row records independent
support/access/offering/ingestion/parser/rules/freshness dimensions and explicit
failure reasons. Rows are stored in the existing isolated ARB state table.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional

import boto3
from boto3.dynamodb.conditions import Key

DIMENSIONS = (
    "provider_support",
    "subscription_access",
    "current_offering",
    "ingestion_health",
    "parser_support",
    "settlement_verification",
    "freshness",
)

ALLOWED_STATES = {
    "SUPPORTED", "UNSUPPORTED", "UNKNOWN", "AVAILABLE", "UNAVAILABLE",
    "ENTITLED", "ACCESS_DENIED", "HEALTHY", "FAILED", "STALE", "FRESH",
    "IMPLEMENTED", "MISSING", "VERIFIED", "UNVERIFIED", "INCOMPATIBLE",
    "OUT_OF_SEASON", "NOT_CURRENTLY_OFFERED", "PARSER_MISSING", "RULES_PENDING",
}


def _table():
    name = os.environ.get("ARB_STATE_TABLE", "").strip()
    if not name:
        raise RuntimeError("ARB_STATE_TABLE not configured")
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
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def canonical_identity(row: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "provider": str(row.get("provider") or "").strip().lower(),
        "sport": str(row.get("sport") or "").strip().lower(),
        "competition": str(row.get("competition") or "").strip().lower(),
        "event": str(row.get("event") or "*").strip().lower() or "*",
        "book": str(row.get("book") or "*").strip().lower() or "*",
        "jurisdiction": str(row.get("jurisdiction") or "*").strip().lower() or "*",
        "market_family": str(row.get("market_family") or "*").strip().lower() or "*",
        "market": str(row.get("market") or "*").strip().lower() or "*",
        "period": str(row.get("period") or "*").strip().lower() or "*",
    }


def coverage_id(row: Mapping[str, Any]) -> str:
    identity = canonical_identity(row)
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def _validate_dimensions(row: Mapping[str, Any]) -> None:
    for field in DIMENSIONS:
        value = row.get(field)
        if value is None:
            continue
        state = str(value).strip().upper()
        if state and state not in ALLOWED_STATES:
            raise ValueError(f"invalid {field} state: {state}")


def normalize(row: Mapping[str, Any], *, now_ms: Optional[int] = None) -> Dict[str, Any]:
    identity = canonical_identity(row)
    if not identity["provider"] or not identity["sport"]:
        raise ValueError("coverage row requires provider and sport")
    _validate_dimensions(row)
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    out: Dict[str, Any] = {
        **identity,
        "coverage_id": coverage_id(identity),
        "first_observed_at_ms": int(row.get("first_observed_at_ms") or now),
        "last_observed_at_ms": int(row.get("last_observed_at_ms") or now),
        "last_successful_fetch_at_ms": row.get("last_successful_fetch_at_ms"),
        "failure_reason": str(row.get("failure_reason") or "")[:500],
        "evidence": dict(row.get("evidence") or {}),
    }
    for field in DIMENSIONS:
        value = row.get(field)
        out[field] = str(value or "UNKNOWN").strip().upper()
    return out


def put(row: Mapping[str, Any], *, table=None, now_ms: Optional[int] = None) -> Dict[str, Any]:
    target = table or _table()
    normalized = normalize(row, now_ms=now_ms)
    cid = normalized["coverage_id"]
    key = {"pk": "COVERAGE", "sk": f"ROW#{cid}"}
    existing = target.get_item(Key=key, ConsistentRead=True).get("Item")
    if existing:
        old = _plain(existing.get("data") or {})
        if old.get("first_observed_at_ms"):
            normalized["first_observed_at_ms"] = int(old["first_observed_at_ms"])
    item = {
        **key,
        "entity": "COVERAGE",
        "coverage_id": cid,
        "updated_at_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "provider": normalized["provider"],
        "sport": normalized["sport"],
        "book": normalized["book"],
        "market_family": normalized["market_family"],
        "data": _ddb(normalized),
    }
    target.put_item(Item=item)
    return normalized


def put_many(rows: Iterable[Mapping[str, Any]], *, table=None, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
    target = table or _table()
    return [put(row, table=target, now_ms=now_ms) for row in rows]


def get(cid: str, *, table=None) -> Optional[Dict[str, Any]]:
    target = table or _table()
    item = target.get_item(Key={"pk": "COVERAGE", "sk": f"ROW#{cid}"}, ConsistentRead=True).get("Item")
    return _plain(item.get("data")) if item else None


def list_rows(*, limit: int = 100, table=None) -> List[Dict[str, Any]]:
    target = table or _table()
    result = target.query(
        KeyConditionExpression=Key("pk").eq("COVERAGE") & Key("sk").begins_with("ROW#"),
        Limit=max(1, min(int(limit), 500)),
        ScanIndexForward=False,
    )
    return [_plain(item.get("data") or {}) for item in result.get("Items", [])]
