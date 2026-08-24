#!/usr/bin/env python3
"""Reconcile MLB prospective backlog while preserving readable status bodies.

The protected lock status route intentionally may return a non-2xx application
status while still carrying the exact official-schedule lifecycle body needed to
decide whether a protected reconciliation is required. V4 discarded that body
inside the generic API-Gateway adapter and therefore failed before it could make
the status-first decision. This v5 adapter accepts non-2xx application bodies
only for the read-only lock-status GET route. Mutating lock, settlement, trainer,
promotion, and production-authority calls retain the existing fail-closed 2xx
requirement and v4 backpressure behavior.

For failed mutating calls, a strict whitelist of bounded diagnostic fields is
included in the raised error. This exposes the exact canonical-settlement blocker
without treating a 409 as success, continuing to another slate, or writing any
storage directly.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Mapping

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as v4

VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5-readable-incomplete-status"
STATUS_PATH = "/v1/mlb/locks/status"
SAFE_APPLICATION_FIELDS = (
    "error",
    "reason",
    "status",
    "overall_status",
    "lockStatus",
    "lifecycleStatus",
    "slateDateEt",
    "slate_date_et",
    "run",
    "authoritativeSettlement",
    "officialGameCount",
    "officialFinalCount",
    "canonicalLockCount",
    "rejectedCanonicalLockCount",
    "terminalNoPredictionCount",
    "lockTerminalConflictCount",
    "terminalNoPredictionExcludedCount",
    "skippedNotFinalCount",
    "missingCanonicalLockCount",
    "identityRejectionCount",
    "labelWriteCount",
    "labelCreatedCount",
    "labelIdempotentCount",
    "labelPolicyDriftIdempotentCount",
    "labelWouldCreateCount",
    "labelConflictCount",
    "immutablePregameRowsMutated",
)
SAFE_FAILURE_COLLECTIONS = (
    "rejectedCanonicalLocks",
    "rejectedTerminalOutcomes",
    "lockTerminalConflictOfficialGamePks",
    "missingCanonicalLocks",
    "identityRejections",
    "labelWrites",
    "immutablePregameReadbackErrors",
)
SAFE_FAILURE_ROW_FIELDS = (
    "officialGamePk",
    "sourcePk",
    "sourceSk",
    "status",
    "reason",
    "error",
    "errors",
    "candidateCount",
    "lockedTeams",
    "officialTeams",
    "existingSettlementFingerprint",
    "proposedSettlementFingerprint",
    "existingImmutableFactsFingerprint",
    "proposedImmutableFactsFingerprint",
)
MAX_DIAGNOSTIC_ITEMS = 8
MAX_DIAGNOSTIC_STRING = 480


def _is_read_only_status_event(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("httpMethod") or "").upper() == "GET"
        and str(event.get("path") or "") == STATUS_PATH
    )


def _bounded_string(value: Any) -> str:
    return str(value)[:MAX_DIAGNOSTIC_STRING]


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_string(value)
    if isinstance(value, (tuple, list)):
        return [_safe_scalar(item) for item in value[:MAX_DIAGNOSTIC_ITEMS]]
    return _bounded_string(value)


def _safe_failure_row(value: Any) -> Any:
    if isinstance(value, Mapping):
        row: Dict[str, Any] = {}
        for key in SAFE_FAILURE_ROW_FIELDS:
            item = value.get(key)
            if item not in (None, "", [], {}):
                row[key] = _safe_scalar(item)
        return row or {"diagnostic": "failure_row_redacted"}
    return _safe_scalar(value)


def _safe_failure_sample(values: Any) -> list[Any]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
        return []
    return [
        _safe_failure_row(value)
        for value in list(values)[:MAX_DIAGNOSTIC_ITEMS]
    ]


def _safe_application_detail(
    application_status: int,
    application: Mapping[str, Any],
    event: Mapping[str, Any],
) -> str:
    detail: Dict[str, Any] = {
        "applicationStatusCode": application_status,
        "eventKind": (
            "read_only_lock_status"
            if _is_read_only_status_event(event)
            else str(event.get("run") or "mutation_or_settlement")
        ),
    }
    for key in SAFE_APPLICATION_FIELDS:
        value = application.get(key)
        if value not in (None, "", [], {}):
            detail[key] = _safe_scalar(value)
    for key in SAFE_FAILURE_COLLECTIONS:
        values = application.get(key)
        if values not in (None, "", [], {}):
            detail[f"{key}Sample"] = _safe_failure_sample(values)
            if isinstance(values, (list, tuple)):
                detail[f"{key}ObservedCount"] = len(values)
    return json.dumps(detail, sort_keys=True, default=str, separators=(",", ":"))


def invoke_json_preserving_status_body(
    lambda_client: Any,
    function_name: str,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Perform one SDK delivery and preserve only read-only status error bodies."""
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    status_code = base._integer(response.get("StatusCode"), field="lambda_status_code")
    payload_bytes = base._read_lambda_payload(response)
    if status_code != 200:
        raise base.ReconciliationError("lambda_invoke_status_not_200")
    if response.get("FunctionError"):
        raise base.ReconciliationError("lambda_function_error")

    payload = base._json_object(
        payload_bytes.decode("utf-8"),
        error="lambda_response_json_invalid",
    )
    if "statusCode" not in payload:
        return payload

    application_status = base._integer(
        payload.get("statusCode"), field="application_status_code"
    )
    body = payload.get("body")
    application = (
        base._json_object(body, error="application_body_json_invalid")
        if isinstance(body, str)
        else dict(body or {})
    )
    if 200 <= application_status < 300:
        return application
    if not _is_read_only_status_event(event):
        raise base.ReconciliationError(
            "lambda_application_status_not_success:"
            + _safe_application_detail(application_status, application, event)
        )

    # Preserve exact provider/status evidence without declaring it healthy.
    # V4 still validates official authority, exact date, counts, and terminal
    # coverage. Only the recognized incomplete-terminal errors may trigger the
    # protected mutation; 5xx/unhealthy bodies remain fail-closed.
    application = dict(application)
    application["_applicationStatusCode"] = application_status
    application["_nonSuccessStatusBodyPreserved"] = True
    return application


@contextmanager
def _status_body_adapter():
    original = base.invoke_json
    base.invoke_json = invoke_json_preserving_status_body
    try:
        yield
    finally:
        base.invoke_json = original


def reconcile(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    with _status_body_adapter():
        value = v4.reconcile(*args, **kwargs)
    value = dict(value)
    value["version"] = VERSION
    value["readOnlyNonSuccessStatusBodiesPreserved"] = True
    value["mutatingNonSuccessStatusesStillFailClosed"] = True
    value["mutatingFailureDiagnosticsWhitelisted"] = True
    return value


def main() -> int:
    args = base._parser().parse_args()
    session = base.boto3.session.Session(region_name=args.region)
    cloudformation = session.client(
        "cloudformation",
        config=v4.control_plane_config(),
    )
    lambda_client = session.client(
        "lambda",
        config=v4.durable_lambda_config(),
    )
    try:
        report = reconcile(
            cloudformation,
            lambda_client,
            stack_name=args.stack_name,
            max_slate_days=args.max_slate_days,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "stackName": args.stack_name,
            "error": f"{type(exc).__name__}:{exc}",
            "readOnlyNonSuccessStatusBodiesPreserved": True,
            "mutatingNonSuccessStatusesStillFailClosed": True,
            "mutatingFailureDiagnosticsWhitelisted": True,
            "directTableWrite": False,
            "postStartPredictionCreationAllowed": False,
            "immutablePredictionRewriteAllowed": False,
            "promotionAuthorityChanged": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
        base._write_report(args.output, report)
        print(json.dumps(report, sort_keys=True), file=sys.stderr)
        return 1
    base._write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
