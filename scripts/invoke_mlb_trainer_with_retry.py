#!/usr/bin/env python3
"""Invoke the AWS MLB trainer with bounded, ambiguity-safe retries."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


RETRY_CONTROL_VERSION = "MLB-TRAINER-DEPLOY-INVOKE-RETRY-v1"
RETRYABLE_EXECUTION_MODES = frozenset(
    {"scheduled", "selection_capture", "manual_review"}
)
STATUS_MODE = "status"
SUPPORTED_MODES = RETRYABLE_EXECUTION_MODES | {STATUS_MODE}
MAX_FAILURES_PER_RETRY_CLASS = 20
MAX_BACKOFF_SECONDS = 60
MINIMUM_LEASE_RETRY_DEADLINE_SECONDS = 1020
DEFAULT_LEASE_RETRY_DEADLINE_SECONDS = 1200
DEFAULT_LEASE_RETRY_DELAY_SECONDS = 20
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

PRE_ADMISSION_CAPACITY_TOKENS = frozenset(
    {
        "TooManyRequests",
        "TooManyRequestsException",
        "ConcurrentInvocationLimit",
        "ConcurrentInvocationLimitExceeded",
        "Rate Exceeded",
    }
)
LEASE_ERROR_TYPE = "ExecutionLeaseUnavailable"
LEASE_ERROR_MESSAGE = (
    "another MLB ML trainer invocation holds the execution lease"
)


class TrainerInvokeError(RuntimeError):
    """Base class for terminal trainer invocation failures."""


class DeployInvokeError(TrainerInvokeError):
    """Value-free deploy invocation failure safe to print in CI."""


class TrainerInvokeRetryExhausted(TrainerInvokeError):
    """Raised when one independently bounded safe retry class is exhausted."""

    def __init__(
        self,
        retry_class: str,
        *,
        invocation_attempts: int,
        pre_admission_failures: int,
        lease_contention_failures: int,
    ) -> None:
        self.retry_class = retry_class
        self.invocation_attempts = invocation_attempts
        self.pre_admission_failures = pre_admission_failures
        self.lease_contention_failures = lease_contention_failures
        super().__init__(
            "AWS MLB trainer invoke exhausted the bounded "
            f"{retry_class} retry budget after {invocation_attempts} attempts"
        )


class TrainerInvocationFunctionError(TrainerInvokeError):
    """Raised for an admitted Lambda function error that must not be retried."""


class TrainerInvocationResponseError(TrainerInvokeError):
    """Raised when Lambda returns non-canonical success evidence."""


def _backoff_seconds(failure_number: int) -> int:
    if failure_number < 1:
        raise ValueError("failure_number must be positive")
    return min(MAX_BACKOFF_SECONDS, 5 * (2 ** (failure_number - 1)))


def _normalized_error_token(value: Any) -> str:
    return " ".join(str(value or "").strip().rstrip(".").split())


def _pre_admission_capacity_kind(exc: BaseException) -> Optional[str]:
    if not isinstance(exc, ClientError):
        return None
    error = (exc.response or {}).get("Error") or {}
    for field in ("Code", "Type", "Reason", "Message"):
        token = _normalized_error_token(error.get(field))
        if token in PRE_ADMISSION_CAPACITY_TOKENS:
            return token
    return None


def _parse_json_object(value: Any, *, error: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise DeployInvokeError(error) from exc
    if not isinstance(parsed, dict):
        raise DeployInvokeError(error)
    return parsed


def _read_json_object(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise DeployInvokeError("status_source_read_failed") from exc
    return _parse_json_object(raw, error="status_source_json_invalid")


def _validated_run_id(value: Any, *, field: str) -> str:
    run_id = str(value or "").strip()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise DeployInvokeError(f"{field}_invalid")
    return run_id


def build_status_request(
    training_result: Dict[str, Any],
    selection_capture_result: Dict[str, Any],
) -> Dict[str, str]:
    if training_result.get("executionMode") != "training":
        raise DeployInvokeError("training_result_execution_mode_invalid")
    if selection_capture_result.get("executionMode") != "selection_capture":
        raise DeployInvokeError("selection_capture_result_execution_mode_invalid")
    return {
        "mode": STATUS_MODE,
        "trainingRunId": _validated_run_id(
            training_result.get("runId"), field="training_run_id"
        ),
        "selectionCaptureRunId": _validated_run_id(
            selection_capture_result.get("runId"),
            field="selection_capture_run_id",
        ),
    }


def _payload_mode(payload: str) -> str:
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("trainer payload must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("trainer payload must be a JSON object")
    mode = parsed.get("mode")
    if not isinstance(mode, str) or mode not in SUPPORTED_MODES:
        raise ValueError("trainer payload mode is unsupported")
    return mode


def _validate_compatibility_policy(
    *,
    mode: str,
    max_attempts: int,
    retry_execution_lease: bool,
    lease_retry_deadline_seconds: int,
    lease_retry_delay_seconds: int,
) -> None:
    if max_attempts != MAX_FAILURES_PER_RETRY_CLASS:
        raise DeployInvokeError("retry_budget_contract_mismatch")
    if retry_execution_lease and mode not in RETRYABLE_EXECUTION_MODES:
        raise DeployInvokeError("execution_lease_retry_mode_invalid")
    if retry_execution_lease and (
        lease_retry_deadline_seconds < MINIMUM_LEASE_RETRY_DEADLINE_SECONDS
    ):
        raise DeployInvokeError("execution_lease_retry_deadline_too_short")
    if retry_execution_lease and lease_retry_delay_seconds <= 0:
        raise DeployInvokeError("execution_lease_retry_delay_invalid")


def _read_payload_stream(response: Dict[str, Any]) -> bytes:
    payload_stream = response.get("Payload")
    if payload_stream is None or not hasattr(payload_stream, "read"):
        raise TrainerInvocationResponseError(
            "Lambda invoke response payload stream is missing"
        )
    close = getattr(payload_stream, "close", None)
    if not callable(close):
        raise TrainerInvocationResponseError(
            "Lambda invoke response payload stream is not closeable"
        )
    try:
        response_body = payload_stream.read()
    finally:
        close()
    if not isinstance(response_body, bytes):
        raise TrainerInvocationResponseError(
            "Lambda invoke response payload is not bytes"
        )
    return response_body


def _function_error_payload(response_body: bytes) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _is_exact_lease_contention(
    response_body: bytes, invocation_metadata: Dict[str, Any]
) -> bool:
    if (
        invocation_metadata.get("StatusCode") != 200
        or invocation_metadata.get("FunctionError") != "Unhandled"
    ):
        return False
    payload = _function_error_payload(response_body)
    return bool(
        payload is not None
        and payload.get("errorType") == LEASE_ERROR_TYPE
        and payload.get("errorMessage") == LEASE_ERROR_MESSAGE
    )


def _validate_canonical_success(
    response_body: bytes, invocation_metadata: Dict[str, Any]
) -> None:
    if invocation_metadata.get("StatusCode") != 200:
        raise TrainerInvocationResponseError(
            "Lambda invoke did not return HTTP status 200"
        )
    if invocation_metadata.get("FunctionError"):
        raise TrainerInvocationFunctionError(
            "AWS MLB trainer returned a non-retryable Lambda function error"
        )
    payload = _function_error_payload(response_body)
    if payload is None:
        raise TrainerInvocationResponseError(
            "AWS MLB trainer response is not a JSON object"
        )
    if payload.get("ok") is not True:
        raise TrainerInvocationResponseError(
            "AWS MLB trainer response is not healthy"
        )


def _retry_evidence(
    *,
    mode: str,
    invocation_attempts: int,
    pre_admission_failures: int,
    lease_contention_failures: int,
) -> Dict[str, Any]:
    return {
        "version": RETRY_CONTROL_VERSION,
        "mode": mode,
        "retryEnabled": True,
        "preAdmissionCapacityRetryEnabled": True,
        "executionLeaseRetryEnabled": mode in RETRYABLE_EXECUTION_MODES,
        "maxFailuresPerClass": MAX_FAILURES_PER_RETRY_CLASS,
        "invocationAttempts": invocation_attempts,
        "preAdmissionCapacityFailures": pre_admission_failures,
        "executionLeaseContentionFailures": lease_contention_failures,
    }


def invoke_with_retry(
    *,
    client: Any,
    function_name: str,
    payload: str,
    max_attempts: int = MAX_FAILURES_PER_RETRY_CLASS,
    retry_execution_lease: bool = False,
    lease_retry_deadline_seconds: int = DEFAULT_LEASE_RETRY_DEADLINE_SECONDS,
    lease_retry_delay_seconds: int = DEFAULT_LEASE_RETRY_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Tuple[bytes, Dict[str, Any]]:
    """Retry only failures proven safe before any state write."""

    mode = _payload_mode(payload)
    _validate_compatibility_policy(
        mode=mode,
        max_attempts=max_attempts,
        retry_execution_lease=retry_execution_lease,
        lease_retry_deadline_seconds=lease_retry_deadline_seconds,
        lease_retry_delay_seconds=lease_retry_delay_seconds,
    )
    capacity_retry_enabled = mode in SUPPORTED_MODES
    lease_retry_enabled = mode in RETRYABLE_EXECUTION_MODES
    encoded_payload = payload.encode("utf-8")
    invocation_attempts = 0
    pre_admission_failures = 0
    lease_contention_failures = 0
    lease_deadline: Optional[float] = None

    while True:
        invocation_attempts += 1
        try:
            response = client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=encoded_payload,
            )
        except ClientError as exc:
            capacity_kind = _pre_admission_capacity_kind(exc)
            if not capacity_retry_enabled or capacity_kind is None:
                raise
            pre_admission_failures += 1
            if pre_admission_failures >= MAX_FAILURES_PER_RETRY_CLASS:
                raise TrainerInvokeRetryExhausted(
                    "pre_admission_capacity",
                    invocation_attempts=invocation_attempts,
                    pre_admission_failures=pre_admission_failures,
                    lease_contention_failures=lease_contention_failures,
                ) from exc
            delay = _backoff_seconds(pre_admission_failures)
            print(
                "AWS MLB trainer invocation was rejected before admission; "
                f"retrying capacity failure {pre_admission_failures}/"
                f"{MAX_FAILURES_PER_RETRY_CLASS} in {delay}s ({capacity_kind})",
                file=sys.stderr,
            )
            if lease_deadline is not None:
                lease_deadline += delay
            sleep(delay)
            continue

        if not isinstance(response, dict):
            raise TrainerInvocationResponseError(
                "Lambda invoke response is not an object"
            )
        response_body = _read_payload_stream(response)
        invocation_metadata = {
            key: response[key]
            for key in ("StatusCode", "FunctionError", "ExecutedVersion")
            if key in response
        }

        if (
            lease_retry_enabled
            and _is_exact_lease_contention(response_body, invocation_metadata)
        ):
            lease_contention_failures += 1
            if lease_contention_failures >= MAX_FAILURES_PER_RETRY_CLASS:
                raise TrainerInvokeRetryExhausted(
                    "execution_lease_contention",
                    invocation_attempts=invocation_attempts,
                    pre_admission_failures=pre_admission_failures,
                    lease_contention_failures=lease_contention_failures,
                )
            now = monotonic()
            if lease_deadline is None:
                lease_deadline = now + lease_retry_deadline_seconds
            delay = _backoff_seconds(lease_contention_failures)
            if now + delay > lease_deadline:
                raise DeployInvokeError("execution_lease_retry_deadline_exceeded")
            print(
                "AWS MLB trainer invocation was admitted but the exact shared "
                "execution lease was busy; retrying lease contention "
                f"{lease_contention_failures}/{MAX_FAILURES_PER_RETRY_CLASS} "
                f"in {delay}s",
                file=sys.stderr,
            )
            sleep(delay)
            continue

        _validate_canonical_success(response_body, invocation_metadata)
        invocation_metadata["InvocationRetryControl"] = _retry_evidence(
            mode=mode,
            invocation_attempts=invocation_attempts,
            pre_admission_failures=pre_admission_failures,
            lease_contention_failures=lease_contention_failures,
        )
        return response_body, invocation_metadata


def _evidence_temporary(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _validate_evidence_paths(response_path: Path, invocation_path: Path) -> None:
    resolved_paths = {
        path.resolve()
        for path in (
            response_path,
            invocation_path,
            _evidence_temporary(response_path),
            _evidence_temporary(invocation_path),
        )
    }
    if len(resolved_paths) != 4:
        raise ValueError("response, invocation, and temporary paths must be distinct")


def _clean_evidence(*paths: Path) -> None:
    cleanup_error: Optional[BaseException] = None
    for path in paths:
        for candidate in (path, _evidence_temporary(path)):
            try:
                candidate.unlink(missing_ok=True)
            except Exception as exc:
                cleanup_error = cleanup_error or exc
    if cleanup_error is not None:
        raise DeployInvokeError("lambda_evidence_cleanup_failed") from cleanup_error


def _persist_canonical_evidence(
    *,
    response_path: Path,
    response_body: bytes,
    invocation_path: Path,
    invocation_metadata: Dict[str, Any],
) -> None:
    _validate_evidence_paths(response_path, invocation_path)
    response_temporary = _evidence_temporary(response_path)
    invocation_temporary = _evidence_temporary(invocation_path)
    _clean_evidence(response_path, invocation_path)
    _validate_canonical_success(response_body, invocation_metadata)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    invocation_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response_temporary.write_bytes(response_body)
        invocation_temporary.write_bytes(
            (json.dumps(invocation_metadata, sort_keys=True) + "\n").encode(
                "utf-8"
            )
        )
        response_temporary.replace(response_path)
        invocation_temporary.replace(invocation_path)
    except BaseException:
        _clean_evidence(response_path, invocation_path)
        raise


def _request_from_args(args: argparse.Namespace) -> Tuple[str, Dict[str, Any]]:
    status_sources = (
        args.status_training_result,
        args.status_selection_capture_result,
    )
    if args.payload is not None:
        if any(status_sources):
            raise DeployInvokeError("lambda_request_sources_conflict")
        _payload_mode(args.payload)
        return args.payload, json.loads(args.payload)
    if not all(status_sources):
        raise DeployInvokeError("lambda_request_source_missing")
    request = build_status_request(
        _read_json_object(args.status_training_result),
        _read_json_object(args.status_selection_capture_result),
    )
    return json.dumps(request, sort_keys=True), request


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--status-training-result", type=Path)
    parser.add_argument("--status-selection-capture-result", type=Path)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--invocation", type=Path, required=True)
    parser.add_argument(
        "--max-attempts", type=int, default=MAX_FAILURES_PER_RETRY_CLASS
    )
    parser.add_argument("--retry-execution-lease", action="store_true")
    parser.add_argument(
        "--deadline-seconds",
        type=int,
        default=DEFAULT_LEASE_RETRY_DEADLINE_SECONDS,
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=int,
        default=DEFAULT_LEASE_RETRY_DELAY_SECONDS,
    )
    args = parser.parse_args(argv)

    _validate_evidence_paths(args.response, args.invocation)
    succeeded = False
    try:
        _clean_evidence(args.response, args.invocation)
        payload, request = _request_from_args(args)
        _validate_compatibility_policy(
            mode=str(request.get("mode") or ""),
            max_attempts=args.max_attempts,
            retry_execution_lease=args.retry_execution_lease,
            lease_retry_deadline_seconds=args.deadline_seconds,
            lease_retry_delay_seconds=args.retry_delay_seconds,
        )
        client = boto3.client(
            "lambda",
            region_name=args.region,
            config=Config(
                connect_timeout=10,
                read_timeout=1000,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )
        response_body, invocation_metadata = invoke_with_retry(
            client=client,
            function_name=args.function_name,
            payload=payload,
            max_attempts=args.max_attempts,
            retry_execution_lease=args.retry_execution_lease,
            lease_retry_deadline_seconds=args.deadline_seconds,
            lease_retry_delay_seconds=args.retry_delay_seconds,
        )
        _persist_canonical_evidence(
            response_path=args.response,
            response_body=response_body,
            invocation_path=args.invocation,
            invocation_metadata=invocation_metadata,
        )
        succeeded = True
    finally:
        if not succeeded:
            _clean_evidence(args.response, args.invocation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
