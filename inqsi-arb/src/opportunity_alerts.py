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


def _first_present(row: Dict[str, Any], *keys: str) -> Any:
    # A legitimate zero margin, profit, or American price must not disappear.
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def _status(bucket: str, row: Dict[str, Any]) -> str:
    # Engine hits include executable mathematical arbs with *unknown* house
    # rules. Do not describe those as verified opportunities in SMS.
    if bucket != "hits":
        return "HELD_BACK"
    validation = row.get("validation") or {}
    settlement = validation.get("settlement_states") or {}
    verified = (
        row.get("arb") is True
        and row.get("executable") is True
        and validation.get("outcome_coverage") == "complete"
        and validation.get("rules_compatible") is True
        and settlement.get("strict_arbitrage") is True
    )
    return "VERIFIED" if verified else "HELD_BACK"


def _holdback_reason(row: Dict[str, Any]) -> str:
    validation = row.get("validation") or {}
    return str(
        _first_present(validation, "qualification_reason", "settlement_state_reason", "settlement_reason")
        or row.get("reason")
        or "EXECUTION_OR_SETTLEMENT_NOT_VERIFIED"
    )


def _identity(status: str, row: Dict[str, Any]) -> str:
    # Actual engine fields are margin_pct and leg.american. Include price,
    # edge, and classification to re-alert material changes while deduping
    # unchanged scans regardless of their audit ID.
    material = {
        "status": status,
        "reason": _holdback_reason(row) if status == "HELD_BACK" else None,
        "sport": row.get("sport"),
        "event_id": _first_present(row, "event_id", "event"),
        "market_id": row.get("market_id"),
        "market": row.get("market"),
        "line": _first_present(row, "line", "point"),
        "margin": _first_present(row, "margin_pct", "margin", "edge", "arb_pct"),
        "legs": [
            {
                "outcome": _first_present(leg, "outcome", "name"),
                "book": _first_present(leg, "book", "bookmaker"),
                "price": _first_present(leg, "american", "american_odds", "price", "odds", "decimal"),
                "point": leg.get("point"),
            }
            for leg in _legs(row)
        ],
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _message(status: str, row: Dict[str, Any], *, audit_event_id: str | None) -> Dict[str, Any]:
    legs = _legs(row)
    return {
        "type": "ARB_OPPORTUNITY",
        "status": status,
        "classification": "VERIFIED_EXECUTABLE" if status == "VERIFIED" else "HELD_BACK",
        "audit_event_id": audit_event_id,
        "sport": row.get("sport"),
        "event_id": _first_present(row, "event_id", "event"),
        "event": row.get("event"),
        "market_id": row.get("market_id"),
        "market": row.get("market"),
        "commence_time": row.get("commence_time"),
        "margin": _first_present(row, "margin_pct", "margin", "edge", "arb_pct"),
        "margin_pct": _first_present(row, "margin_pct", "margin", "edge", "arb_pct"),
        "profit": _first_present(row, "minimum_profit", "min_profit", "profit", "guaranteed_profit"),
        "stake_total": _first_present(row, "allocated_stake", "stake_total", "bankroll"),
        "detected_at": _first_present(row, "detected_at", "fetched_at", "timestamp"),
        "quote_timestamps": [leg.get("last_update") for leg in legs],
        "holdback_reason": _holdback_reason(row) if status == "HELD_BACK" else None,
        "legs": legs,
        "places_bets": False,
    }


def publish_scan_opportunities(result: Dict[str, Any], *, audit_event_id: str | None = None) -> Dict[str, int]:
    if not enabled():
        return {"published": 0, "deduped": 0, "errors": 0}

    rows: Iterable[tuple[str, Dict[str, Any]]] = [
        *((_status("hits", row), row) for row in (result.get("hits") or [])),
        *((_status("detected_unverified", row), row) for row in (result.get("detected_unverified") or [])),
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
