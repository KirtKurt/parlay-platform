"""Near-real-time ARB opportunity notification emission."""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable

try:
    import boto3
except ImportError:  # local tests
    boto3 = None

from audit_store import claim_once, release_claim


def _topic_arn() -> str:
    return os.environ.get("ARB_OPPORTUNITY_TOPIC_ARN", "").strip()


def enabled() -> bool:
    return bool(_topic_arn() and boto3 is not None)


def _legs(row: Dict[str, Any]) -> list:
    return list(row.get("legs") or row.get("quotes") or [])


def _positive_finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _is_verified_executable(row: Dict[str, Any]) -> bool:
    """Require the same strict profit and settlement proof as the ARB console."""
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
        and _positive_finite(row.get("minimum_profit"))
        and _positive_finite(settlement.get("minimum_net_pnl"))
        and not validation.get("qualification_reason")
        and not validation.get("settlement_reason")
        and not validation.get("settlement_state_reason")
        and not validation.get("settlement_advisory")
        and not validation.get("missing_books")
    )


def _holdback_reason(row: Dict[str, Any]) -> str:
    validation = row.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    return str(
        row.get("reason")
        or validation.get("qualification_reason")
        or validation.get("settlement_reason")
        or validation.get("settlement_state_reason")
        or validation.get("settlement_advisory")
        or ("SPORTSBOOK_SETTLEMENT_RULES_MISSING" if validation.get("missing_books") else None)
        or "EXECUTABLE_QUALIFICATION_NOT_PROVEN"
    )


def _money_identity(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return round(value, 2)
    return value


def _identity(status: str, row: Dict[str, Any]) -> str:
    # Include material price/edge state so a changed opportunity can re-alert,
    # while identical repeated scans and Lambda retries deduplicate.
    material = {
        "status": status,
        # A new settlement/qualification holdback reason is a material status
        # change even when the books, prices, and margin have not moved.
        "holdback_reason": _holdback_reason(row) if status == "HELD_BACK" else None,
        "sport": row.get("sport"),
        "event_id": row.get("event_id") or row.get("event"),
        "market": row.get("market"),
        "line": row.get("line") if row.get("line") is not None else row.get("point"),
        "margin": row.get("margin_pct") if row.get("margin_pct") is not None else (row.get("margin") or row.get("edge") or row.get("arb_pct")),
        # A changed feasible stake or guaranteed profit is material even when
        # the displayed book prices and percentage margin stay unchanged.
        # Normalize currency to cents to avoid alerts for float jitter.
        "stake_total": _money_identity(row.get("allocated_stake")),
        "minimum_profit": _money_identity(row.get("minimum_profit")),
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



def _sms_text(status: str, row: Dict[str, Any], message: Dict[str, Any]) -> str:
    """Readable, bounded SMS; the default SNS payload retains complete JSON."""
    def clean(value: Any, limit: int = 80) -> str:
        return " ".join(str(value if value is not None else "?").split())[:limit]

    def american(leg: Dict[str, Any]) -> str:
        # Never present decimal prices as American without conversion.
        value = leg.get("american")
        if value is None:
            value = leg.get("american_odds")
        if value is None:
            value = leg.get("price")
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                value = None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) >= 100:
            return f"{value:+.0f}" if value > 0 else f"{value:.0f}"
        decimal = leg.get("decimal")
        if isinstance(decimal, (int, float)) and not isinstance(decimal, bool) and math.isfinite(decimal) and decimal > 1:
            odds = 100 * (decimal - 1) if decimal >= 2 else -100 / (decimal - 1)
            return f"{odds:+.0f}" if odds > 0 else f"{odds:.0f}"
        return "odds?"

    title = "Inqsi ARB VERIFIED EXECUTABLE" if status == "VERIFIED" else "Inqsi ARB HELD BACK"
    event = row.get("event") or message.get("event_id") or "event?"
    parts = [title, clean(message.get("sport"), 40), clean(event, 100), clean(message.get("market"), 60)]
    legs = [
        f"{clean(leg.get('book') or leg.get('bookmaker'), 30)} "
        f"{clean(leg.get('outcome') or leg.get('name'), 30)} {american(leg)}"
        for leg in _legs(row)[:6]
        if isinstance(leg, dict)
    ]
    if legs:
        parts.append(" / ".join(legs))
    if message.get("margin") is not None:
        unit = "%" if row.get("margin_pct") is not None else ""
        parts.append(f"edge/margin {clean(message['margin'], 20)}{unit}")
    if message.get("stake_total") is not None:
        parts.append(f"stake {clean(message['stake_total'], 24)}")
    if message.get("profit") is not None:
        parts.append(f"profit {clean(message['profit'], 24)}")
    parts.append(f"detected {clean(message.get('detected_at'), 40)}")
    if message.get("quote_last_updates"):
        parts.append(f"quote {clean(message['quote_last_updates'][0], 40)}")
    if status != "VERIFIED":
        parts.append(f"reason {clean(message.get('holdback_reason'), 120)}")
    # SNS permits up to 1,600 characters for a single SMS publish; keep the
    # readable text bounded while leaving full details in the default payload.
    sms = " | ".join(parts)
    return sms if len(sms) <= 1000 else sms[:997] + "..."


def publish_scan_opportunities(result: Dict[str, Any], *, audit_event_id: str | None = None,
                               sport: str | None = None) -> Dict[str, int]:
    if not enabled():
        return {"published": 0, "deduped": 0, "errors": 0}

    # Normalize candidates individually. A non-object provider candidate must
    # not crash the entire scan before the per-row publish safety boundary.
    rows: list[tuple[str, Dict[str, Any]]] = []
    errors = 0
    for candidates, forced_status in (
        (result.get("hits") or [], None),
        (result.get("detected_unverified") or [], "HELD_BACK"),
    ):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                errors += 1
                continue
            status = forced_status or (
                "VERIFIED" if _is_verified_executable(candidate) else "HELD_BACK"
            )
            rows.append((status, {**candidate, "sport": candidate.get("sport") or sport}))
    client = boto3.client("sns")
    published = deduped = 0
    ttl = int(os.environ.get("ARB_OPPORTUNITY_ALERT_DEDUPE_SECONDS", "21600"))
    for status, row in rows:
        claim_token = uuid.uuid4().hex
        claimed = False
        publish_attempted = False
        try:
            # A malformed candidate must not abort publication of subsequent
            # independently qualified opportunities. Fail this row closed
            # before claiming a dedupe identity or contacting SNS.
            identity = _identity(status, row)
            if not claim_once("ARB_OPPORTUNITY", identity, ttl_seconds=ttl,
                              claim_token=claim_token):
                deduped += 1
                continue
            claimed = True
            message = _message(status, row, audit_event_id=audit_event_id)
            # Prepare every local field before attempting SNS. If malformed
            # provider data makes formatting fail, SNS was definitely not
            # called and the dedupe claim can safely be released for a retry.
            publish_kwargs = {
                "TopicArn": _topic_arn(),
                "Subject": f"Inqsi ARB {status}: {message.get('sport') or 'sport'} {message.get('market') or 'market'}"[:100],
                "MessageStructure": "json",
                "Message": json.dumps({
                    "default": json.dumps(message, sort_keys=True, default=str),
                    "sms": _sms_text(status, row, message),
                }, sort_keys=True),
                "MessageAttributes": {
                    "status": {"DataType": "String", "StringValue": status},
                    "sport": {"DataType": "String", "StringValue": str(message.get("sport") or "unknown")},
                },
            }
            publish_attempted = True
            response = client.publish(**publish_kwargs)
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
        except Exception as exc:
            # Local pre-publish formatting failures definitely sent nothing,
            # so release only our claim. Once SNS was attempted, release only
            # on definitive rejections; ambiguous outcomes retain the claim.
            response = getattr(exc, "response", {}) or {}
            code = str((response.get("Error") or {}).get("Code") or "")
            rejected = code in {
                "AuthorizationError", "AuthorizationErrorException",
                "InvalidParameter", "InvalidParameterValue",
                "NotFound", "NotFoundException", "TopicLimitExceeded",
                "Throttling", "ThrottlingException", "TooManyRequestsException",
            }
            if claimed and (not publish_attempted or rejected):
                try:
                    release_claim("ARB_OPPORTUNITY", identity, claim_token=claim_token)
                except Exception:
                    # Preserve the original publish failure; never pretend
                    # the failed release proves the SMS was delivered.
                    pass
            errors += 1
    return {"published": published, "deduped": deduped, "errors": errors}
