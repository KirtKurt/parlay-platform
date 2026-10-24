"""Near-real-time ARB opportunity notification emission."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
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


def _is_verified_executable(row: Dict[str, Any]) -> bool:
    """Do not trust membership in hits as proof of sportsbook-rule compatibility."""
    validation = row.get("validation")
    if not isinstance(validation, dict):
        return False
    settlement = validation.get("settlement_states")
    return bool(
        row.get("arb") is True
        and row.get("math_arb") is True
        and row.get("executable") is True
        and validation.get("outcome_coverage") == "complete"
        and validation.get("rules_compatible") is True
        and validation.get("executable") is True
        and isinstance(settlement, dict)
        and settlement.get("strict_arbitrage") is True
        and not validation.get("qualification_reason")
    )


def _holdback_reason(row: Dict[str, Any]) -> str:
    validation = row.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    return str(
        row.get("reason")
        or validation.get("qualification_reason")
        or validation.get("settlement_reason")
        or validation.get("settlement_state_reason")
        or "EXECUTABLE_QUALIFICATION_NOT_PROVEN"
    )


def _identity(status: str, row: Dict[str, Any]) -> str:
    # Include material price/edge state so a changed opportunity can re-alert,
    # while identical repeated scans and Lambda retries deduplicate.
    material = {
        "status": status,
        "sport": row.get("sport"),
        "event_id": row.get("event_id") or row.get("event"),
        "market": row.get("market"),
        "line": row.get("line") or row.get("point"),
        "margin": row.get("margin_pct") if row.get("margin_pct") is not None else (row.get("margin") or row.get("edge") or row.get("arb_pct")),
        "legs": [
            {
                "outcome": leg.get("outcome") or leg.get("name"),
                "book": leg.get("book") or leg.get("bookmaker"),
                "price": leg.get("american") if leg.get("american") is not None else (leg.get("american_odds") or leg.get("price") or leg.get("odds") or leg.get("decimal")),
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
        "margin": row.get("margin_pct") if row.get("margin_pct") is not None else (row.get("margin") or row.get("edge") or row.get("arb_pct")),
        "profit": row.get("minimum_profit") if row.get("minimum_profit") is not None else (row.get("profit") or row.get("min_profit") or row.get("guaranteed_profit")),
        "stake_total": row.get("allocated_stake") if row.get("allocated_stake") is not None else (row.get("stake_total") or row.get("bankroll")),
        "detected_at": row.get("detected_at") or row.get("fetched_at") or row.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "quote_last_updates": [leg["last_update"] for leg in _legs(row) if leg.get("last_update")],
        "holdback_reason": _holdback_reason(row) if status == "HELD_BACK" else None,
        "legs": _legs(row),
        "places_bets": False,
    }


def publish_scan_opportunities(result: Dict[str, Any], *, audit_event_id: str | None = None,
                               sport: str | None = None) -> Dict[str, int]:
    if not enabled():
        return {"published": 0, "deduped": 0, "errors": 0}

    rows: Iterable[tuple[str, Dict[str, Any]]] = [
        *(("VERIFIED" if _is_verified_executable(row) else "HELD_BACK",
           {**row, "sport": row.get("sport") or sport}) for row in (result.get("hits") or [])),
        *(("HELD_BACK", {**row, "sport": row.get("sport") or sport})
          for row in (result.get("detected_unverified") or [])),
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
            response = client.publish(
                TopicArn=_topic_arn(),
                Subject=f"Inqsi ARB {status}: {message.get('sport') or 'sport'} {message.get('market') or 'market'}"[:100],
                Message=json.dumps(message, sort_keys=True, default=str),
                MessageAttributes={
                    "status": {"DataType": "String", "StringValue": status},
                    "sport": {"DataType": "String", "StringValue": str(message.get("sport") or "unknown")},
                },
            )
            message_id = response.get("MessageId") if isinstance(response, dict) else None
            if not isinstance(message_id, str) or not message_id.strip():
                # A successful SNS publication must return a MessageId. Do not
                # count an ambiguous response as delivery proof.
                raise RuntimeError("SNS publish returned no MessageId")
            print(json.dumps({
                "event": "ARB_SNS_PUBLISH_CONFIRMED",
                "message_id": message_id,
                "identity": identity,
                "status": status,
            }, sort_keys=True))
            published += 1
        except Exception:
            errors += 1
    return {"published": published, "deduped": deduped, "errors": errors}
