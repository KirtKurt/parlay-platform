"""Near-real-time ARB opportunity notification emission."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable

try:
    import boto3
except ImportError:  # local tests
    boto3 = None

from audit_store import claim_once


def _topic_arn() -> str:
    return os.environ.get("ARB_OPPORTUNITY_TOPIC_ARN", "").strip()


def enabled() -> bool:
    return bool(_topic_arn() and boto3 is not None)


def _legs(row: Dict[str, Any]) -> list:
    return list(row.get("legs") or row.get("quotes") or [])


def _identity(status: str, row: Dict[str, Any]) -> str:
    # Include material price/edge state so a changed opportunity can re-alert,
    # while identical repeated scans and Lambda retries deduplicate.
    material = {
        "status": status,
        "sport": row.get("sport"),
        "event_id": row.get("event_id") or row.get("event"),
        "market": row.get("market"),
        "line": row.get("line") or row.get("point"),
        "margin": row.get("margin") or row.get("edge") or row.get("arb_pct"),
        "legs": [
            {
                "outcome": leg.get("outcome") or leg.get("name"),
                "book": leg.get("book") or leg.get("bookmaker"),
                "price": leg.get("american_odds") or leg.get("price") or leg.get("odds"),
                "point": leg.get("point"),
            }
            for leg in _legs(row)
        ],
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _message(status: str, row: Dict[str, Any], *, audit_event_id: str | None) -> Dict[str, Any]:
    return {
        "type": "ARB_OPPORTUNITY",
        "status": status,
        "audit_event_id": audit_event_id,
        "sport": row.get("sport"),
        "event_id": row.get("event_id") or row.get("event"),
        "market": row.get("market"),
        "margin": row.get("margin") or row.get("edge") or row.get("arb_pct"),
        "profit": row.get("profit") or row.get("min_profit") or row.get("guaranteed_profit"),
        "stake_total": row.get("stake_total") or row.get("bankroll"),
        "detected_at": row.get("detected_at") or row.get("fetched_at") or row.get("timestamp"),
        "holdback_reason": row.get("reason") if status == "HELD_BACK" else None,
        "legs": _legs(row),
        "places_bets": False,
    }


def publish_scan_opportunities(result: Dict[str, Any], *, audit_event_id: str | None = None) -> Dict[str, int]:
    if not enabled():
        return {"published": 0, "deduped": 0, "errors": 0}

    rows: Iterable[tuple[str, Dict[str, Any]]] = [
        *(("VERIFIED", row) for row in (result.get("hits") or [])),
        *(("HELD_BACK", row) for row in (result.get("detected_unverified") or [])),
    ]
    client = boto3.client("sns")
    published = deduped = errors = 0
    ttl = int(os.environ.get("ARB_OPPORTUNITY_ALERT_DEDUPE_SECONDS", "21600"))
    for status, row in rows:
        identity = _identity(status, row)
        try:
            if not claim_once("ARB_OPPORTUNITY", identity, ttl_seconds=ttl):
                deduped += 1
                continue
            message = _message(status, row, audit_event_id=audit_event_id)
            client.publish(
                TopicArn=_topic_arn(),
                Subject=f"Inqsi ARB {status}: {message.get('sport') or 'sport'} {message.get('market') or 'market'}"[:100],
                Message=json.dumps(message, sort_keys=True, default=str),
                MessageAttributes={
                    "status": {"DataType": "String", "StringValue": status},
                    "sport": {"DataType": "String", "StringValue": str(message.get("sport") or "unknown")},
                },
            )
            published += 1
        except Exception:
            errors += 1
    return {"published": published, "deduped": deduped, "errors": errors}
