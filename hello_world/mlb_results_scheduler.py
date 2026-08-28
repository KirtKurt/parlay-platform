import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import mlb_canonical_final_labels_v1 as canonical_settlement
from mlb_audit import (
    final_mlb_scores_report,
    settlement_proof_report as legacy_settlement_proof_report,
    settle_mlb_slate as legacy_settle_mlb_slate,
)
from mlb_signal_learning import build_signal_learning_report
from mlb_result_signals import (
    RESULT_SIGNAL_PRODUCER_AUTHORITY,
    RESULT_SIGNAL_PRODUCER_PROOF_VERSION,
    build_result_signals,
    latest_result_signals,
)


_NATIVE_EVENTBRIDGE_KEYS = {
    "version",
    "id",
    "detail-type",
    "source",
    "account",
    "time",
    "region",
    "resources",
    "detail",
}
_EVENTBRIDGE_RULE_ARN = re.compile(
    r"^arn:(?:aws|aws-[a-z-]+):events:([a-z0-9-]+):(\d{12}):rule/(.+)$"
)


def _json_default(value: Any) -> Any:
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
    except Exception:
        pass
    return str(value)


def _native_eventbridge_provenance(
    event: Dict[str, Any],
    context: Any,
) -> Dict[str, str] | None:
    """Validate provenance for native Schedule events; preserve plain directs."""

    present = _NATIVE_EVENTBRIDGE_KEYS.intersection(event)
    if not present:
        return None
    missing = sorted(_NATIVE_EVENTBRIDGE_KEYS - set(event))
    if missing:
        raise ValueError(f"Native EventBridge envelope is incomplete: {missing}")
    if event.get("version") != "0":
        raise ValueError("Native EventBridge envelope version mismatch")
    if event.get("source") != "aws.events":
        raise ValueError("Native EventBridge envelope source mismatch")
    if event.get("detail-type") != "Scheduled Event":
        raise ValueError("Native EventBridge envelope detail-type mismatch")
    if event.get("detail") != {}:
        raise ValueError("Native EventBridge scheduled detail must be empty")

    event_id = str(event.get("id") or "").strip()
    request_id = str(getattr(context, "aws_request_id", "") or "").strip()
    try:
        uuid.UUID(event_id)
        uuid.UUID(request_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("Native EventBridge/Lambda request ID is invalid") from exc

    event_time_raw = str(event.get("time") or "").strip()
    try:
        event_time = datetime.fromisoformat(event_time_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Native EventBridge event time is invalid") from exc
    if event_time.tzinfo is None:
        raise ValueError("Native EventBridge event time lacks a timezone")
    event_time_utc = (
        event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    resources = event.get("resources")
    if not isinstance(resources, list) or len(resources) != 1:
        raise ValueError("Native EventBridge envelope must name exactly one rule")
    rule_arn = str(resources[0] or "").strip()
    arn_match = _EVENTBRIDGE_RULE_ARN.fullmatch(rule_arn)
    if arn_match is None:
        raise ValueError("Native EventBridge rule ARN is invalid")
    region = str(event.get("region") or "").strip()
    account = str(event.get("account") or "").strip()
    if region != arn_match.group(1) or account != arn_match.group(2):
        raise ValueError("Native EventBridge rule/account/region binding mismatch")

    return {
        "schema_version": RESULT_SIGNAL_PRODUCER_PROOF_VERSION,
        "authority": RESULT_SIGNAL_PRODUCER_AUTHORITY,
        "lambda_request_id": request_id,
        "event_id": event_id,
        "event_time_utc": event_time_utc,
        "event_source": "aws.events",
        "detail_type": "Scheduled Event",
        "rule_arn": rule_arn,
        "account": account,
        "region": region,
    }


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _http_method(event: Dict[str, Any]) -> str:
    """Return the API Gateway REST/v2 method without classifying internal events."""
    method = event.get("httpMethod")
    request_context = event.get("requestContext")
    if not method and isinstance(request_context, dict):
        http_context = request_context.get("http")
        if isinstance(http_context, dict):
            method = http_context.get("method")
    return str(method or "").strip().upper()


def _http_path(event: Dict[str, Any]) -> str:
    path = event.get("path") or event.get("rawPath")
    request_context = event.get("requestContext")
    if not path and isinstance(request_context, dict):
        http_context = request_context.get("http")
        if isinstance(http_context, dict):
            path = http_context.get("path")
    return str(path or "")


def _is_http_event(event: Dict[str, Any]) -> bool:
    """Fail closed for recognizable REST API, HTTP API v2, Function URL, or ALB events."""
    if "httpMethod" in event or "rawPath" in event:
        return True
    request_context = event.get("requestContext")
    if not isinstance(request_context, dict):
        return False
    if isinstance(request_context.get("http"), dict):
        return True
    return any(
        key in request_context
        for key in ("apiId", "resourceId", "routeKey", "elb")
    )


def _method_not_allowed(method: str, path: str) -> Dict[str, Any]:
    response = _resp(
        405,
        {
            "ok": False,
            "sport": "mlb",
            "error": "HTTP mutation methods are disabled; use GET for read-only reports",
            "method": method or "MISSING",
            "path": path,
        },
    )
    response["headers"]["allow"] = "GET,OPTIONS"
    return response


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    payload.update(event.get("queryStringParameters") or {})
    payload.update(_parse_body(event))
    for key in (
        "slate_date_et",
        "slate_date",
        "date",
        "days_from",
        "daysFrom",
        "fetch_scores",
        "store",
        "legacy_diagnostic",
    ):
        if key in event and key not in payload:
            payload[key] = event[key]
    return payload


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _settlement_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    days_from = payload.get("days_from")
    if days_from is None:
        days_from = payload.get("daysFrom")
    if days_from is None:
        days_from = 3
    return {
        "slate_date": (
            payload.get("slate_date_et")
            or payload.get("slate_date")
            or payload.get("date")
        ),
        "days_from": int(days_from),
        "fetch_scores": _bool(payload.get("fetch_scores"), True),
    }


def _legacy_diagnostic(
    args: Dict[str, Any],
    *,
    proof: bool = False,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Preserve the former settlement surface without granting it authority."""
    if not enabled:
        return {
            "ok": True,
            "executed": False,
            "authoritative": False,
            "status": "LEGACY_DIAGNOSTIC_DISABLED",
        }
    try:
        report = (
            legacy_settlement_proof_report(**args)
            if proof
            else legacy_settle_mlb_slate(**args)
        )
        return {
            "ok": bool(report.get("ok")) if isinstance(report, dict) else False,
            "executed": True,
            "authoritative": False,
            "report": report,
        }
    except Exception as exc:
        return {
            "ok": False,
            "executed": True,
            "authoritative": False,
            "error": f"{type(exc).__name__}:{exc}",
        }


def _canonical_with_legacy_diagnostic(
    report: Dict[str, Any],
    args: Dict[str, Any],
    *,
    proof: bool = False,
    enabled: bool = True,
) -> Dict[str, Any]:
    out = dict(report)
    out["legacyDiagnosticCompatibility"] = _legacy_diagnostic(
        args,
        proof=proof,
        enabled=enabled,
    )
    out["settlementAuthority"] = "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK"
    out["legacyDiagnosticIsAuthoritative"] = False
    return out


def lambda_handler(event, context):
    event = event if isinstance(event, dict) else {}
    method = _http_method(event)
    path = _http_path(event)
    is_http = _is_http_event(event)

    if is_http and method == "OPTIONS":
        return _resp(200, {"ok": True})
    if is_http and method != "GET":
        # Only methodless trusted/internal invocations may reach authoritative
        # write paths. Any recognizable HTTP request without GET fails closed.
        return _method_not_allowed(method, path)

    try:
        payload = _payload(event)
        args = _settlement_args(payload)
        slate_date = args.get("slate_date")

        if method == "GET" and path in {"/v1/mlb/scores/final", "/v1/results/mlb/final-scores"}:
            # The legacy fetch path persists outcomes. Public reads therefore
            # use only rows already stored by the authoritative scheduled job.
            read_args = {**args, "fetch_scores": False}
            return _resp(200, final_mlb_scores_report(**read_args))

        if method == "GET" and path in {"/v1/results/mlb/proof", "/v1/mlb/settlement/proof_report"}:
            proof_args = {
                **args,
                "fetch_scores": _bool(payload.get("fetch_scores"), False),
            }
            canonical = canonical_settlement.settlement_proof_report(**proof_args)
            report = _canonical_with_legacy_diagnostic(
                canonical,
                proof_args,
                proof=True,
                enabled=False,
            )
            return _resp(200 if canonical.get("ok") else 409, report)

        if method == "GET" and path in {"/v1/results/mlb/settlement", "/v1/mlb/settlement/slate"}:
            canonical = canonical_settlement.settle_mlb_slate(
                **args,
                store=False,
            )
            report = _canonical_with_legacy_diagnostic(
                canonical,
                args,
                enabled=False,
            )
            return _resp(200 if canonical.get("ok") else 409, report)

        if method == "GET" and path in {"/v1/results/mlb/signal-learning", "/v1/mlb/signal-learning"}:
            # fetch_scores=True delegates to the legacy outcome puller, which
            # writes. The public learning view is stored-evidence-only.
            learn_args = {**args, "fetch_scores": False}
            return _resp(200, build_signal_learning_report(**learn_args))

        if method == "GET" and path in {"/v1/results/mlb/result-signals", "/v1/mlb/result-signals"}:
            if not slate_date:
                return _resp(
                    400,
                    {
                        "ok": False,
                        "sport": "mlb",
                        "error": "date, slate_date, or slate_date_et is required",
                    },
                )
            # build/store flags are intentionally ignored on public reads.
            return _resp(200, latest_result_signals(slate_date))

        # EventBridge and trusted direct Lambda invocation are methodless. This
        # is the only entrypoint that can create canonical labels or result
        # signal rows.
        if not is_http:
            producer_provenance = _native_eventbridge_provenance(event, context)
            settlement = (
                canonical_settlement.settle_mlb_slate(**args, store=True)
                if args.get("slate_date")
                else canonical_settlement.settle_recent_mlb_slates(
                    days_from=args.get("days_from", 3),
                    fetch_scores=args.get("fetch_scores", True),
                    store=True,
                )
            )
            # The former settlement mutates legacy rows. It is never invoked by
            # the scheduled authoritative path.
            legacy = _legacy_diagnostic(args, enabled=False)
            resolved_slate = (
                args.get("slate_date")
                or settlement.get("slateDateEt")
                or ((legacy.get("report") or {}).get("slate_date_et"))
            )
            learning = build_signal_learning_report(
                slate_date=resolved_slate,
                days_from=args.get("days_from", 3),
                fetch_scores=False,
            )
            result_signals = (
                build_result_signals(
                    resolved_slate,
                    fetch_scores=False,
                    store=True,
                    **(
                        {"producer_provenance": producer_provenance}
                        if producer_provenance is not None
                        else {}
                    ),
                )
                if resolved_slate
                else {
                    "ok": False,
                    "error": "No slate_date available for result signals",
                }
            )
            report = {
                **settlement,
                "settlementAuthority": "CANONICAL_IMMUTABLE_LOCK_OFFICIAL_GAME_PK",
                "legacyDiagnosticIsAuthoritative": False,
                "legacyDiagnosticCompatibility": legacy,
                "signalLearningDiagnostic": learning,
                "resultSignalsDiagnostic": result_signals,
                "signal_learning": learning,
                "result_signals": result_signals,
            }
            return _resp(200 if settlement.get("ok") else 409, report)

        return _resp(
            404,
            {
                "ok": False,
                "sport": "mlb",
                "error": f"Route not found: {method} {path}",
            },
        )
    except Exception as exc:
        return _resp(
            500,
            {
                "ok": False,
                "sport": "mlb",
                "error": str(exc),
            },
        )
