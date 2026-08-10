#!/usr/bin/env python3
"""Build durable, public-safe evidence for one deployed MLB trainer invocation.

The normal deployment verifier intentionally fails closed when the AWS trainer is
unhealthy. This utility preserves enough redacted evidence to diagnose that
failure without printing environment values, credentials, raw provider payloads,
or changing any model, promotion, inference, or production authority.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


VERSION = "MLB-TRAINER-DEPLOY-DIAGNOSTIC-v1-redacted-fail-closed"
PROOF_TYPE = "MLB_TRAINER_DEPLOY_HEALTH_DIAGNOSTIC"
REDACTED = "[REDACTED]"
MAX_DEPTH = 7
MAX_MAPPING_ITEMS = 120
MAX_SEQUENCE_ITEMS = 120
MAX_STRING_LENGTH = 2000
SENSITIVE_KEY_FRAGMENTS = (
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "cookie",
    "signature",
    "session",
    "privatekey",
    "private_key",
    "accesskey",
    "access_key",
    "apikey",
    "api_key",
    "token",
)
AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
QUERY_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|authorization)=([^&\s]+)"
)

TRAINING_FIELDS = (
    "ok",
    "status",
    "executionMode",
    "runId",
    "createdAtUtc",
    "version",
    "experimentId",
    "releaseCutoffUtc",
    "acceptedRowCount",
    "rejectedRowCount",
    "rejectionReasonCounts",
    "partitionCounts",
    "rowsRequired",
    "canonicalSlateContinuity",
    "milestones",
    "modelTrained",
    "championChanged",
    "automaticPromotionEnabled",
    "liveInferenceAuthority",
    "productionAuthorityChanged",
    "waiting",
    "waitReason",
    "continuityWaitCompatibilityVersion",
    "failure",
    "training",
    "selectionCaptureBeforeTraining",
    "prospectiveSelectionLedger",
    "deploymentIdentity",
    "errorType",
    "errorMessage",
    "stackTrace",
)
LATEST_RUN_FIELDS = (
    "ok",
    "status",
    "executionMode",
    "runId",
    "createdAtUtc",
    "failure",
    "acceptedRowCount",
    "rejectedRowCount",
    "rejectionReasonCounts",
    "partitionCounts",
    "canonicalSlateContinuity",
    "milestones",
    "modelTrained",
    "championChanged",
    "automaticPromotionEnabled",
    "liveInferenceAuthority",
    "productionAuthorityChanged",
    "deploymentIdentity",
)
CONFIGURATION_FIELDS = (
    "FunctionName",
    "FunctionArn",
    "Runtime",
    "Handler",
    "CodeSize",
    "Description",
    "Timeout",
    "MemorySize",
    "LastModified",
    "CodeSha256",
    "Version",
    "State",
    "StateReasonCode",
    "LastUpdateStatus",
    "LastUpdateStatusReasonCode",
    "RevisionId",
    "PackageType",
    "Architectures",
    "EphemeralStorage",
)
INVOCATION_FIELDS = (
    "StatusCode",
    "FunctionError",
    "ExecutedVersion",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(value: Any) -> bool:
    key = str(value or "").lower().replace("-", "_")
    compact = re.sub(r"[^a-z0-9_]", "", key)
    return any(fragment in compact for fragment in SENSITIVE_KEY_FRAGMENTS)


def _redact_string(value: str) -> str:
    text = AWS_ACCESS_KEY_RE.sub(REDACTED, value)
    text = BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)
    if len(text) > MAX_STRING_LENGTH:
        return text[:MAX_STRING_LENGTH] + "...[TRUNCATED]"
    return text


def redact(value: Any, *, key_hint: str = "", depth: int = 0) -> Any:
    """Return a bounded JSON-safe value with secret-bearing fields removed."""

    if key_hint and _is_sensitive_key(key_hint):
        return REDACTED
    if depth > MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, bytes):
        return _redact_string(value.decode("utf-8", errors="replace"))
    if isinstance(value, Mapping):
        output: Dict[str, Any] = {}
        items = list(value.items())[:MAX_MAPPING_ITEMS]
        for raw_key, raw_item in items:
            key = str(raw_key)
            output[key] = redact(raw_item, key_hint=key, depth=depth + 1)
        if len(value) > MAX_MAPPING_ITEMS:
            output["__truncatedMappingItems"] = len(value) - MAX_MAPPING_ITEMS
        return output
    if isinstance(value, (list, tuple)):
        output = [
            redact(item, depth=depth + 1)
            for item in list(value)[:MAX_SEQUENCE_ITEMS]
        ]
        if len(value) > MAX_SEQUENCE_ITEMS:
            output.append(
                {"__truncatedSequenceItems": len(value) - MAX_SEQUENCE_ITEMS}
            )
        return output
    return _redact_string(str(value))


def _load_json(path: Path) -> tuple[Optional[Any], Optional[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        return None, f"read_failed:{type(exc).__name__}"
    try:
        return json.loads(raw), None
    except Exception as exc:
        return None, f"json_invalid:{type(exc).__name__}"


def _allowlisted(value: Any, fields: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        field: redact(value.get(field), key_hint=field)
        for field in fields
        if field in value
    }


def _decode_log_tail(invocation: Any) -> Optional[str]:
    if not isinstance(invocation, Mapping):
        return None
    encoded = invocation.get("LogResult")
    if not encoded:
        return None
    try:
        raw = base64.b64decode(str(encoded), validate=True).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return "[LOG_TAIL_DECODE_FAILED]"
    lines = raw.splitlines()[-80:]
    return _redact_string("\n".join(lines))


def _invocation_summary(value: Any, parse_error: Optional[str]) -> Dict[str, Any]:
    output = _allowlisted(value, INVOCATION_FIELDS)
    output["parseError"] = parse_error
    log_tail = _decode_log_tail(value)
    if log_tail is not None:
        output["redactedLogTail"] = log_tail
    return output


def _health_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    latest = value.get("latestRun")
    return {
        "ok": value.get("ok"),
        "errors": redact(value.get("errors"), key_hint="errors"),
        "executionMode": value.get("executionMode"),
        "deploymentIdentityMatches": value.get("deploymentIdentityMatches"),
        "latestRun": _allowlisted(latest, LATEST_RUN_FIELDS),
    }


def _status_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    manifest = value.get("manifest") if isinstance(value.get("manifest"), Mapping) else {}
    return {
        "ok": value.get("ok"),
        "version": value.get("version"),
        "experimentId": value.get("experimentId"),
        "releaseCutoffUtc": value.get("releaseCutoffUtc"),
        "manifest": {
            "phase": manifest.get("phase"),
            "revision": manifest.get("revision"),
            "manifestDigest": manifest.get("manifestDigest"),
            "prospectiveTestSealed": manifest.get("prospectiveTestSealed"),
            "frozenChallengerBound": bool(manifest.get("frozenChallenger")),
        },
        "trainingHealth": _health_summary(value.get("trainingHealth")),
        "selectionCaptureHealth": _health_summary(value.get("selectionCaptureHealth")),
        "automaticPromotionEnabled": value.get("automaticPromotionEnabled"),
        "firstPromotionRequiresManualReview": value.get(
            "firstPromotionRequiresManualReview"
        ),
        "v2InferenceConsumerInstalled": value.get("v2InferenceConsumerInstalled"),
        "runtimeAuthorityActivationAvailable": value.get(
            "runtimeAuthorityActivationAvailable"
        ),
    }


def _configuration_summary(value: Any) -> Dict[str, Any]:
    output = _allowlisted(value, CONFIGURATION_FIELDS)
    variables = {}
    if isinstance(value, Mapping):
        environment = value.get("Environment")
        if isinstance(environment, Mapping):
            raw_variables = environment.get("Variables")
            if isinstance(raw_variables, Mapping):
                variables = raw_variables
    output["environment"] = {
        "variableNames": sorted(str(key) for key in variables),
        "valuesRedacted": True,
    }
    return output


def _classification(
    training: Any,
    training_invocation: Any,
    *,
    training_parse_error: Optional[str],
    invocation_parse_error: Optional[str],
) -> str:
    if invocation_parse_error:
        return "TRAINER_INVOCATION_METADATA_UNREADABLE"
    if not isinstance(training_invocation, Mapping):
        return "TRAINER_INVOCATION_METADATA_INVALID"
    if int(training_invocation.get("StatusCode") or 0) != 200:
        return "TRAINER_LAMBDA_INVOKE_NON_200"
    if training_invocation.get("FunctionError"):
        return "TRAINER_LAMBDA_FUNCTION_ERROR"
    if training_parse_error:
        return "TRAINER_RESPONSE_UNREADABLE"
    if not isinstance(training, Mapping):
        return "TRAINER_RESPONSE_INVALID"
    if training.get("ok") is True:
        return "TRAINER_HEALTHY"
    status = str(training.get("status") or "UNKNOWN").strip().upper()
    return f"TRAINER_RESPONSE_UNHEALTHY:{status}"


def build_report(
    *,
    training: Any,
    training_parse_error: Optional[str],
    training_invocation: Any,
    training_invocation_parse_error: Optional[str],
    status: Any,
    status_parse_error: Optional[str],
    status_invocation: Any,
    status_invocation_parse_error: Optional[str],
    configuration: Any,
    configuration_parse_error: Optional[str],
    source_sha: str,
    workflow_run_id: str,
) -> Dict[str, Any]:
    classification = _classification(
        training,
        training_invocation,
        training_parse_error=training_parse_error,
        invocation_parse_error=training_invocation_parse_error,
    )
    training_summary = _allowlisted(training, TRAINING_FIELDS)
    status_summary = _status_summary(status)
    status_invocation_ok = bool(
        isinstance(status_invocation, Mapping)
        and int(status_invocation.get("StatusCode") or 0) == 200
        and not status_invocation.get("FunctionError")
        and status_parse_error is None
    )
    production_authority_changed = any(
        value is True
        for value in (
            training_summary.get("productionAuthorityChanged"),
            ((status_summary.get("trainingHealth") or {}).get("latestRun") or {}).get(
                "productionAuthorityChanged"
            ),
        )
    )
    return {
        "ok": classification == "TRAINER_HEALTHY" and status_invocation_ok,
        "proofType": PROOF_TYPE,
        "version": VERSION,
        "createdAtUtc": _now_iso(),
        "sourceSha": source_sha,
        "workflowRunId": workflow_run_id,
        "classification": classification,
        "trainingResponse": training_summary,
        "trainingResponseParseError": training_parse_error,
        "trainingInvocation": _invocation_summary(
            training_invocation, training_invocation_parse_error
        ),
        "statusAfter": status_summary,
        "statusResponseParseError": status_parse_error,
        "statusInvocation": _invocation_summary(
            status_invocation, status_invocation_parse_error
        ),
        "lambdaConfiguration": _configuration_summary(configuration),
        "lambdaConfigurationParseError": configuration_parse_error,
        "productionAuthorityChanged": production_authority_changed,
        "secretExposed": False,
        "diagnosticOnly": True,
        "modelOrPromotionGateChanged": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-response", type=Path, required=True)
    parser.add_argument("--training-invocation", type=Path, required=True)
    parser.add_argument("--status-response", type=Path, required=True)
    parser.add_argument("--status-invocation", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    args = parser.parse_args(argv)

    training, training_error = _load_json(args.training_response)
    training_invocation, training_invocation_error = _load_json(
        args.training_invocation
    )
    status, status_error = _load_json(args.status_response)
    status_invocation, status_invocation_error = _load_json(args.status_invocation)
    configuration, configuration_error = _load_json(args.configuration)
    report = build_report(
        training=training,
        training_parse_error=training_error,
        training_invocation=training_invocation,
        training_invocation_parse_error=training_invocation_error,
        status=status,
        status_parse_error=status_error,
        status_invocation=status_invocation,
        status_invocation_parse_error=status_invocation_error,
        configuration=configuration,
        configuration_parse_error=configuration_error,
        source_sha=args.source_sha,
        workflow_run_id=args.workflow_run_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f"{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
