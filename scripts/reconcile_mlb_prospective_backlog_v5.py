#!/usr/bin/env python3
"""Reconcile MLB prospective backlog with settlement-triggered terminal replay.

Read-only lock status bodies may project a missed lifecycle game as terminal
coverage before a durable no-prediction outcome exists. Canonical settlement is
the stronger durability authority. When, and only when, settlement returns the
exact conflict-free 409 shape proving official finals lack a canonical lock or
durable terminal outcome, this adapter invokes the existing protected lock
replay, verifies the exact-date official status, and retries the full bounded
reconciliation. The 409 is never treated as success and no storage is written
directly by this script.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Mapping, Optional

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v3 as v3
import reconcile_mlb_prospective_backlog_v4 as v4

VERSION = (
    "MLB-PROSPECTIVE-BACKLOG-RECONCILIATION-v5.2-"
    "redacted-lambda-function-error-evidence"
)
STATUS_PATH = "/v1/mlb/locks/status"
SETTLEMENT_RUN = "prospective_backlog_settlement_v4"
TERMINAL_REPLAY_RUN = "prospective_terminal_backlog_reconciliation_v5"
MISSING_LOCK_REASON = "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME"
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
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|authorization|password|credential)"
    r"(\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;\]}]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")


class DurableTerminalReplayRequired(base.ReconciliationError):
    """A conflict-free settlement gap requires the protected terminal replay."""

    def __init__(self, slate_date: str, detail: Mapping[str, Any]):
        self.slate_date = slate_date
        self.detail = dict(detail)
        super().__init__(
            "settlement_requires_protected_terminal_replay:"
            + json.dumps(self.detail, sort_keys=True, separators=(",", ":"))
        )


def _is_read_only_status_event(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("httpMethod") or "").upper() == "GET"
        and str(event.get("path") or "") == STATUS_PATH
    )


def _event_kind(event: Mapping[str, Any]) -> str:
    if _is_read_only_status_event(event):
        return "read_only_lock_status"
    return str(event.get("run") or "mutation_or_settlement")


def _event_slate_date(event: Mapping[str, Any]) -> str:
    query = event.get("queryStringParameters")
    query_date = query.get("date") if isinstance(query, Mapping) else None
    return str(
        event.get("slateDateEt")
        or event.get("slate_date")
        or query_date
        or ""
    )


def _redacted_bounded_string(value: Any, *, tail: bool = False) -> str:
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY]", text)
    if tail:
        return text[-MAX_DIAGNOSTIC_STRING:]
    return text[:MAX_DIAGNOSTIC_STRING]


def _bounded_string(value: Any) -> str:
    return _redacted_bounded_string(value)


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
    return [_safe_failure_row(value) for value in list(values)[:MAX_DIAGNOSTIC_ITEMS]]


def _safe_application_detail(
    application_status: int,
    application: Mapping[str, Any],
    event: Mapping[str, Any],
) -> str:
    detail: Dict[str, Any] = {
        "applicationStatusCode": application_status,
        "eventKind": _event_kind(event),
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


def _safe_lambda_function_error_detail(
    function_name: str,
    event: Mapping[str, Any],
    response: Mapping[str, Any],
    payload_bytes: bytes,
) -> str:
    """Return bounded, redacted FunctionError evidence and no request payload."""

    detail: Dict[str, Any] = {
        "functionName": _redacted_bounded_string(function_name),
        "functionError": _redacted_bounded_string(response.get("FunctionError")),
        "eventKind": _event_kind(event),
        "slateDateEt": _event_slate_date(event),
        "requestPayloadIncluded": False,
        "secretExposed": False,
    }
    try:
        parsed = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        parsed = None
        detail["payloadParseError"] = type(exc).__name__
    if isinstance(parsed, Mapping):
        error_type = parsed.get("errorType")
        error_message = parsed.get("errorMessage")
        if error_type not in (None, ""):
            detail["errorType"] = _redacted_bounded_string(error_type)
        if error_message not in (None, ""):
            detail["errorMessage"] = _redacted_bounded_string(error_message)

    encoded_log = response.get("LogResult")
    if encoded_log not in (None, ""):
        try:
            decoded_log = base64.b64decode(str(encoded_log), validate=True).decode(
                "utf-8", errors="replace"
            )
            detail["redactedLogTail"] = _redacted_bounded_string(
                decoded_log,
                tail=True,
            )
        except Exception as exc:
            detail["logTailParseError"] = type(exc).__name__

    return json.dumps(detail, sort_keys=True, default=str, separators=(",", ":"))


def _nonnegative_integer(value: Any, *, field: str) -> int:
    return base._integer(value, field=field)


def _terminal_replay_detail(
    application_status: int,
    application: Mapping[str, Any],
    event: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Recognize only the exact conflict-free durable-terminal settlement gap."""

    if application_status != 409 or str(event.get("run") or "") != SETTLEMENT_RUN:
        return None
    if application.get("authoritativeSettlement") is not True:
        return None
    if str(application.get("status") or application.get("overall_status") or "") != "FAILED_CLOSED":
        return None
    if application.get("immutablePregameRowsMutated") is not False:
        return None

    slate_date = str(event.get("slate_date") or event.get("slateDateEt") or "")
    returned_date = str(
        application.get("slateDateEt") or application.get("slate_date_et") or ""
    )
    if not slate_date or returned_date != slate_date:
        return None

    try:
        official = _nonnegative_integer(application.get("officialGameCount"), field="official_game_count")
        finals = _nonnegative_integer(application.get("officialFinalCount"), field="official_final_count")
        canonical = _nonnegative_integer(application.get("canonicalLockCount"), field="canonical_lock_count")
        terminal = _nonnegative_integer(application.get("terminalNoPredictionCount"), field="terminal_no_prediction_count")
        missing = _nonnegative_integer(application.get("missingCanonicalLockCount"), field="missing_canonical_lock_count")
        rejected = _nonnegative_integer(application.get("rejectedCanonicalLockCount"), field="rejected_canonical_lock_count")
        conflicts = _nonnegative_integer(application.get("lockTerminalConflictCount"), field="lock_terminal_conflict_count")
        identity_rejections = _nonnegative_integer(application.get("identityRejectionCount"), field="identity_rejection_count")
        label_conflicts = _nonnegative_integer(application.get("labelConflictCount"), field="label_conflict_count")
        skipped = _nonnegative_integer(application.get("skippedNotFinalCount"), field="skipped_not_final_count")
    except base.ReconciliationError:
        return None

    if not official or finals != official or skipped:
        return None
    if not missing or rejected or conflicts or identity_rejections or label_conflicts:
        return None
    if canonical + terminal + missing != official:
        return None

    missing_rows = application.get("missingCanonicalLocks") or []
    if not isinstance(missing_rows, list) or len(missing_rows) != missing:
        return None
    if any(
        not isinstance(row, Mapping)
        or not str(row.get("officialGamePk") or "")
        or str(row.get("reason") or "") != MISSING_LOCK_REASON
        for row in missing_rows
    ):
        return None

    return {
        "applicationStatusCode": application_status,
        "slateDateEt": slate_date,
        "officialGameCount": official,
        "officialFinalCount": finals,
        "canonicalLockCount": canonical,
        "terminalNoPredictionCount": terminal,
        "missingCanonicalLockCount": missing,
        "missingOfficialGamePks": [
            str(row.get("officialGamePk")) for row in missing_rows[:MAX_DIAGNOSTIC_ITEMS]
        ],
        "missingOfficialGamePkCount": len(missing_rows),
        "authoritativeSettlement": True,
        "conflictFree": True,
        "immutablePregameRowsMutated": False,
    }


def invoke_json_preserving_status_body(
    lambda_client: Any,
    function_name: str,
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Perform one SDK delivery; preserve status evidence and signal safe replay."""

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    status_code = base._integer(response.get("StatusCode"), field="lambda_status_code")
    payload_bytes = base._read_lambda_payload(response)
    if status_code != 200:
        raise base.ReconciliationError("lambda_invoke_status_not_200")
    if response.get("FunctionError"):
        raise base.ReconciliationError(
            "lambda_function_error:"
            + _safe_lambda_function_error_detail(
                function_name,
                event,
                response,
                payload_bytes,
            )
        )

    payload = base._json_object(payload_bytes.decode("utf-8"), error="lambda_response_json_invalid")
    if "statusCode" not in payload:
        return payload

    application_status = base._integer(payload.get("statusCode"), field="application_status_code")
    body = payload.get("body")
    application = (
        base._json_object(body, error="application_body_json_invalid")
        if isinstance(body, str)
        else dict(body or {})
    )
    if 200 <= application_status < 300:
        return application
    if not _is_read_only_status_event(event):
        replay_detail = _terminal_replay_detail(application_status, application, event)
        if replay_detail is not None:
            raise DurableTerminalReplayRequired(replay_detail["slateDateEt"], replay_detail)
        raise base.ReconciliationError(
            "lambda_application_status_not_success:"
            + _safe_application_detail(application_status, application, event)
        )

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


def _execute_protected_terminal_replay(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    request: DurableTerminalReplayRequired,
) -> Dict[str, Any]:
    functions = base.resolve_stack_functions(cloudformation, stack_name)
    with _status_body_adapter():
        replay = v4.invoke_json_with_backpressure(
            lambda_client,
            functions.lock,
            {
                "sport": "mlb",
                "run": TERMINAL_REPLAY_RUN,
                "slateDateEt": request.slate_date,
                "force": True,
            },
        )
        status = v4.invoke_json_with_backpressure(
            lambda_client,
            functions.lock,
            {
                "httpMethod": "GET",
                "path": STATUS_PATH,
                "queryStringParameters": {"date": request.slate_date},
            },
        )
    evidence = v3.validate_lock_result(replay, status, request.slate_date)
    return {
        "slateDateEt": request.slate_date,
        "settlementFailure": dict(request.detail),
        "lockEvidence": evidence,
        "protectedLockReplay": True,
        "settlement409TreatedAsSuccess": False,
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
    }


def reconcile(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    stack_name = str(kwargs.get("stack_name") or "")
    if len(args) < 2 or not stack_name:
        raise base.ReconciliationError("reconcile_arguments_invalid")
    cloudformation, lambda_client = args[0], args[1]
    max_replays = int(kwargs.get("max_slate_days") or base.DEFAULT_MAX_SLATE_DAYS)
    repaired: Dict[str, Dict[str, Any]] = {}

    for _ in range(max_replays + 1):
        try:
            with _status_body_adapter():
                value = v4.reconcile(*args, **kwargs)
            break
        except DurableTerminalReplayRequired as request:
            if request.slate_date in repaired:
                raise base.ReconciliationError(
                    "settlement_terminal_replay_failed_to_close_gap:"
                    + request.slate_date
                ) from request
            repaired[request.slate_date] = _execute_protected_terminal_replay(
                cloudformation,
                lambda_client,
                stack_name=stack_name,
                request=request,
            )
    else:
        raise base.ReconciliationError("settlement_terminal_replay_bound_exhausted")

    value = dict(value)
    value["version"] = VERSION
    value["readOnlyNonSuccessStatusBodiesPreserved"] = True
    value["mutatingNonSuccessStatusesStillFailClosed"] = True
    value["mutatingFailureDiagnosticsWhitelisted"] = True
    value["lambdaFunctionErrorsRedacted"] = True
    value["lambdaFunctionErrorRequestPayloadIncluded"] = False
    value["settlementTriggeredProtectedTerminalReplayCount"] = len(repaired)
    value["settlementTriggeredProtectedTerminalReplays"] = list(repaired.values())
    value["settlement409TreatedAsSuccess"] = False
    value["directTableWrite"] = False
    value["postStartPredictionCreationAllowed"] = False
    value["immutablePredictionRewriteAllowed"] = False
    value["promotionAuthorityChanged"] = False
    value["productionAuthorityChanged"] = False
    value["automaticWagerAllowed"] = False
    return value


def main() -> int:
    args = base._parser().parse_args()
    session = base.boto3.session.Session(region_name=args.region)
    cloudformation = session.client("cloudformation", config=v4.control_plane_config())
    lambda_client = session.client("lambda", config=v4.durable_lambda_config())
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
            "lambdaFunctionErrorsRedacted": True,
            "lambdaFunctionErrorRequestPayloadIncluded": False,
            "settlement409TreatedAsSuccess": False,
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
