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
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from typing import Any, Dict, Mapping

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as v4

VERSION = "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5-readable-incomplete-status"
STATUS_PATH = "/v1/mlb/locks/status"


def _is_read_only_status_event(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("httpMethod") or "").upper() == "GET"
        and str(event.get("path") or "") == STATUS_PATH
    )


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
        raise base.ReconciliationError("lambda_application_status_not_success")

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
