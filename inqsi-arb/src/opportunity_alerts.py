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
    # Alert presentation is best-effort for malformed held-back candidates.
    # Keep only mapping legs so identity, timestamp, and SMS formatting cannot
    # suppress the candidate or subsequent valid alerts. Executable proof
    # still checks the original unfiltered legs in _verified_legs_present.
    raw = row.get("legs") or row.get("quotes") or []
    if not isinstance(raw, list):
        return []
    return [leg for leg in raw if isinstance(leg, dict)]


def _positive_finite(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)



def _verified_legs_present(row: Dict[str, Any]) -> bool:
    """Require identified, priced, complete outcomes before an executable SMS."""
    legs = row.get("legs")
    if not isinstance(legs, list) or len(legs) < 2:
        return False
    # A declared three-outcome market cannot be verified using only two
    # priced outcomes, even if upstream coverage flags contradict the legs.
    # Draw-no-bet and ordinary two-way markets remain unaffected.
    market = row.get("market") or row.get("market_key")
    if isinstance(market, str):
        key = market.strip().lower().replace("-", "_")
        three_way = (key in {"1x2", "3_way", "three_way", "h2h_3_way"}
                     or key.endswith("_1x2") or key.startswith("h2h_3_way_"))
        if three_way and len(legs) != 3:
            return False
    outcomes = []
    for leg in legs:
        if not isinstance(leg, dict):
            return False
        outcome, book = leg.get("outcome"), leg.get("book")
        if not isinstance(outcome, str) or not outcome.strip():
            return False
        if not isinstance(book, str) or not book.strip():
            return False
        # The upstream executable flag cannot make an unpriced or invalid
        # sportsbook leg executable. Reject contradictory explicit prices,
        # including NaN, bools, zero and impossible American odds.
        american = leg.get("american")
        if american is None:
            american = leg.get("american_odds")
        if american is None:
            american = leg.get("price")
        if american is not None:
            try:
                numeric = float(american)
            except (TypeError, ValueError, OverflowError):
                return False
            if isinstance(american, bool) or not math.isfinite(numeric) or abs(numeric) < 100:
                return False
        else:
            decimal = leg.get("decimal")
            try:
                numeric = float(decimal)
            except (TypeError, ValueError, OverflowError):
                return False
            if isinstance(decimal, bool) or not math.isfinite(numeric) or numeric <= 1:
                return False
        outcomes.append(outcome.strip())
    if len(set(outcomes)) != len(outcomes):
        return False
    # A three-way market with only two priced legs is not executable, even
    # when a contradictory upstream row says outcome_coverage=complete.
    expected = row.get("outcomes")
    if expected is not None:
        if (not isinstance(expected, list) or len(expected) < 2
                or any(not isinstance(item, str) or not item.strip() for item in expected)):
            return False
        normalized = [item.strip() for item in expected]
        if len(set(normalized)) != len(normalized) or set(normalized) != set(outcomes):
            return False
    return True


def _is_verified_executable(row: Dict[str, Any]) -> bool:
    """Require the same strict profit and settlement proof as the ARB console."""
    validation = row.get("validation")
    if not isinstance(validation, dict):
        return False
    settlement = validation.get("settlement_states")
    return bool(
        row.get("arb") is True
        and _verified_legs_present(row)
        and row.get("math_arb") is True
        and row.get("executable") is True
        and validation.get("outcome_coverage") == "complete"
        and validation.get("rules_compatible") is True
        and validation.get("executable") is True
        and isinstance(settlement, dict)
        and settlement.get("strict_arbitrage") is True
        and _positive_finite(row.get("minimum_profit"))
        and _positive_finite(settlement.get("minimum_net_pnl"))
        # A top-level candidate rejection is contradictory evidence even when
        # a malformed upstream row also carries optimistic validation flags.
        and not row.get("reason")
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
        or ("OPPORTUNITY_LEGS_INCOMPLETE_OR_AMBIGUOUS" if not _verified_legs_present(row) else None)
        or "EXECUTABLE_QUALIFICATION_NOT_PROVEN"
    )


def _first_not_none(row: Dict[str, Any], *keys: str) -> Any:
    """Preserve zero-valued provider fields in alert details and identities."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


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
        # Team labels can repeat (e.g. doubleheaders); preserve provider
        # market identity and scheduled start in the deduplication key.
        "market_id": row.get("market_id"),
        "commence_time": row.get("commence_time"),
        "market": row.get("market"),
        "line": row.get("line") if row.get("line") is not None else row.get("point"),
        "margin": _first_not_none(row, "margin_pct", "margin", "edge", "arb_pct"),
        # A changed feasible stake or guaranteed profit is material even when
        # the displayed book prices and percentage margin stay unchanged.
        # Normalize currency to cents to avoid alerts for float jitter.
        "stake_total": _money_identity(
            row.get("allocated_stake") if row.get("allocated_stake") is not None
            else (row.get("stake_total") if row.get("stake_total") is not None else row.get("bankroll"))
        ),
        "minimum_profit": _money_identity(
            row.get("minimum_profit") if row.get("minimum_profit") is not None
            else (row.get("profit") if row.get("profit") is not None
                  else (row.get("min_profit") if row.get("min_profit") is not None
                        else row.get("guaranteed_profit")))
        ),
        # Provider ordering is not part of the betting opportunity. Sorting
        # canonical leg values prevents duplicate SMS when the same books and
        # outcomes arrive in a different order on a later scan. Price, point,
        # and book changes remain material and generate a new identity.
        "legs": sorted(
            [
                {
                    "outcome": leg.get("outcome") or leg.get("name"),
                    "book": leg.get("book") or leg.get("bookmaker"),
                    "price": leg.get("american") if leg.get("american") is not None else (leg.get("american_odds") or leg.get("price") or leg.get("odds") or leg.get("decimal")),
                    "point": leg.get("point"),
                }
                for leg in _legs(row)
            ],
            key=lambda leg: json.dumps(leg, sort_keys=True, separators=(",", ":"), default=str),
        ),
    }
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _oldest_quote_age_seconds(row: Dict[str, Any], *, as_of: datetime) -> int | None:
    """Report quote age only when every leg has a valid UTC-aware timestamp.

    This is diagnostic metadata, not a replacement for upstream freshness gates.
    An incomplete or future-dated provider clock cannot establish quote age.
    """
    legs = row.get("legs") or row.get("quotes")
    if not isinstance(legs, list) or not legs:
        return None
    ages = []
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        stamp = leg.get("last_update")
        if not isinstance(stamp, str) or not stamp.strip():
            return None
        try:
            updated = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if updated.tzinfo is None:
            return None
        seconds = (as_of - updated).total_seconds()
        if not math.isfinite(seconds) or seconds < 0:
            return None
        ages.append(int(seconds))
    return max(ages)


def _message(status: str, row: Dict[str, Any], *, audit_event_id: str | None) -> Dict[str, Any]:
    return {
        "type": "ARB_OPPORTUNITY",
        "status": status,
        "audit_event_id": audit_event_id,
        "sport": row.get("sport"),
        "event_id": row.get("event_id") or row.get("event"),
        "commence_time": row.get("commence_time"),
        "market": row.get("market"),
        "margin": _first_not_none(row, "margin_pct", "margin", "edge", "arb_pct"),
        "profit": _first_not_none(row, "minimum_profit", "profit", "min_profit", "guaranteed_profit"),
        "stake_total": _first_not_none(row, "allocated_stake", "stake_total", "bankroll"),
        "detected_at": row.get("detected_at") or row.get("fetched_at") or row.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "quote_last_updates": [leg["last_update"] for leg in _legs(row) if leg.get("last_update")],
        "quote_age_seconds": _oldest_quote_age_seconds(row, as_of=datetime.now(timezone.utc)),
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
        if isinstance(decimal, str):
            try:
                decimal = float(decimal.strip())
            except (ValueError, OverflowError):
                decimal = None
        if isinstance(decimal, (int, float)) and not isinstance(decimal, bool) and math.isfinite(decimal) and decimal > 1:
            odds = 100 * (decimal - 1) if decimal >= 2 else -100 / (decimal - 1)
            return f"{odds:+.0f}" if odds > 0 else f"{odds:.0f}"
        return "odds?"

    def line_text(leg: Dict[str, Any]) -> str:
        # A spread/total price without its handicap can describe a different
        # selection. Only show a finite numeric point; never invent a line.
        value = leg.get("point")
        if isinstance(value, bool) or value is None:
            return ""
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            return ""
        return f" line {numeric:+g}" if math.isfinite(numeric) else ""

    title = "Inqsi ARB VERIFIED EXECUTABLE" if status == "VERIFIED" else "Inqsi ARB HELD BACK"
    event = row.get("event") or message.get("event_id") or "event?"
    parts = [title, clean(message.get("sport"), 40), clean(event, 100), clean(message.get("market"), 60)]
    legs = [
        f"{clean(leg.get('book') or leg.get('bookmaker'), 30)} "
        f"{clean(leg.get('outcome') or leg.get('name'), 30)}{line_text(leg)} {american(leg)}"
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
    if message.get('commence_time'):
        parts.append(f"starts {clean(message['commence_time'], 40)}")
    parts.append(f"detected {clean(message.get('detected_at'), 40)}")
    if message.get("quote_last_updates"):
        parts.append(f"quote {clean(message['quote_last_updates'][0], 40)}")
    if message.get("quote_age_seconds") is not None:
        parts.append(f"oldest quote age {clean(message['quote_age_seconds'], 20)}s")
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
        (result.get("hits"), None),
        (result.get("detected_unverified"), "HELD_BACK"),
    ):
        if candidates is None:
            continue
        # An unexpected provider collection (integer, object, or string)
        # must not crash the audited scan or suppress the other collection.
        # Only arrays of individual candidates are accepted.
        if not isinstance(candidates, list):
            errors += 1
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                errors += 1
                continue
            status = forced_status or (
                "VERIFIED" if _is_verified_executable(candidate) else "HELD_BACK"
            )
            rows.append((status, {**candidate, "sport": candidate.get("sport") or sport}))
    # Client construction can fail before any SNS call (for example when
    # region configuration is unavailable). Do not turn an otherwise audited
    # scan into an API failure or acquire claims for messages never attempted.
    if not rows:
        return {"published": 0, "deduped": 0, "errors": errors}
    # Invalid deduplication configuration must fail closed before acquiring
    # any alert claims or attempting SNS publication.
    try:
        ttl = int(os.environ.get("ARB_OPPORTUNITY_ALERT_DEDUPE_SECONDS", "21600"))
        if ttl <= 0:
            raise ValueError("nonpositive dedupe TTL")
    except (TypeError, ValueError, OverflowError):
        print(json.dumps({
            "event": "ARB_SNS_DEDUPE_CONFIG_INVALID",
            "n_candidates": len(rows),
        }, sort_keys=True))
        return {"published": 0, "deduped": 0, "errors": errors + len(rows)}
    try:
        client = boto3.client("sns")
    except Exception as exc:
        print(json.dumps({
            "event": "ARB_SNS_CLIENT_INIT_FAILED",
            "error_type": type(exc).__name__,
            "n_candidates": len(rows),
        }, sort_keys=True))
        return {"published": 0, "deduped": 0, "errors": errors + len(rows)}
    published = deduped = 0
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
            claim_released = False
            if claimed and (not publish_attempted or rejected):
                try:
                    release_claim("ARB_OPPORTUNITY", identity, claim_token=claim_token)
                    claim_released = True
                except Exception:
                    # Preserve the original publish failure; never pretend
                    # the failed release proves the SMS was delivered.
                    pass
            # Log only bounded diagnostic metadata. Exception messages can
            # contain credentials, provider payloads, or customer details.
            # An attempted publish without MessageId is never delivery proof.
            print(json.dumps({
                "event": "ARB_SNS_PUBLISH_FAILED" if publish_attempted else "ARB_SNS_PREPARE_FAILED",
                "identity": identity if claimed else None,
                "status": status,
                "error_type": type(exc).__name__,
                "error_code": code[:80],
                "publish_attempted": publish_attempted,
                "claim_released": claim_released,
            }, sort_keys=True))
            errors += 1
    return {"published": published, "deduped": deduped, "errors": errors}
