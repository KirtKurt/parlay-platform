#!/usr/bin/env python3
"""Invoke the AWS MLB trainer with bounded, mutation-safe retries."""

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


RETRYABLE_ERROR_CODES = frozenset({"TooManyRequestsException"})
RETRYABLE_FUNCTION_ERROR_TYPE = "ExecutionLeaseUnavailable"
LEASE_RETRY_REQUEST_MODES = frozenset({"scheduled", "selection_capture"})
# Nine total admission attempts allow eight capped sleeps (315 seconds max),
# extending the bounded upper retry window past an observed five-minute backlog.
DEFAULT_MAX_ATTEMPTS = 9
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 60
MINIMUM_LEASE_RETRY_DEADLINE_SECONDS = 1020
DEFAULT_LEASE_RETRY_DEADLINE_SECONDS = 1200
DEFAULT_LEASE_RETRY_DELAY_SECONDS = 20
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DeployInvokeError(RuntimeError):
    """Value-free deploy invocation failure safe to print in CI."""


def _error_code(exc: BaseException) -> str:
    if not isinstance(exc, ClientError):
        return ""
    return str(((exc.response or {}).get("Error") or {}).get("Code") or "")


def _backoff_seconds(failed_attempt: int) -> int:
    return min(
        MAX_BACKOFF_SECONDS,
        BASE_BACKOFF_SECONDS * (2 ** max(0, failed_attempt - 1)),
    )


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


def _validate_execution_lease_retry_scope(
    request: Dict[str, Any], *, enabled: bool
) -> None:
    if enabled and request.get("mode") not in LEASE_RETRY_REQUEST_MODES:
        raise DeployInvokeError("execution_lease_retry_mode_invalid")


def build_status_request(
    training_result: Dict[str, Any],
    selection_capture_result: Dict[str, Any],
) -> Dict[str, str]:
    if training_result.get("executionMode") != "training":
        raise DeployInvokeError("training_result_execution_mode_invalid")
    if selection_capture_result.get("executionMode") != "selection_capture":
        raise DeployInvokeError(
            "selection_capture_result_execution_mode_invalid"
        )
    return {
        "mode": "status",
        "trainingRunId": _validated_run_id(
            training_result.get("runId"), field="training_run_id"
        ),
        "selectionCaptureRunId": _validated_run_id(
            selection_capture_result.get("runId"),
            field="selection_capture_run_id",
        ),
    }


def _response_bytes(response: Dict[str, Any]) -> bytes:
    payload_stream = response.get("Payload")
    if payload_stream is None or not hasattr(payload_stream, "read"):
        raise DeployInvokeError("lambda_response_payload_stream_missing")
    try:
        response_body = payload_stream.read()
    finally:
        close = getattr(payload_stream, "close", None)
        if callable(close):
            close()
    if not isinstance(response_body, bytes):
        raise DeployInvokeError("lambda_response_payload_not_bytes")
    return response_body


def invoke_with_retry(
    *,
    client: Any,
    function_name: str,
    payload: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_execution_lease: bool = False,
    lease_retry_deadline_seconds: int = (
        DEFAULT_LEASE_RETRY_DEADLINE_SECONDS
    ),
    lease_retry_delay_seconds: int = DEFAULT_LEASE_RETRY_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Tuple[bytes, Dict[str, Any]]:
    """Retry only provably pre-mutation admission failures.

    ``TooManyRequestsException`` is returned before Lambda admission.  An
    ``ExecutionLeaseUnavailable`` function error is returned after admission,
    but the trainer raises it only when its conditional DynamoDB lease acquire
    fails, before any status or experiment write.  Network/read ambiguity and
    every other function error are terminal and are never replayed.
    """

    if not 1 <= max_attempts <= DEFAULT_MAX_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be between 1 and {DEFAULT_MAX_ATTEMPTS}"
        )
    if retry_execution_lease and (
        lease_retry_deadline_seconds
        < MINIMUM_LEASE_RETRY_DEADLINE_SECONDS
    ):
        raise DeployInvokeError("execution_lease_retry_deadline_too_short")
    if lease_retry_delay_seconds <= 0:
        raise DeployInvokeError("execution_lease_retry_delay_invalid")
    request = _parse_json_object(payload, error="lambda_request_json_invalid")
    _validate_execution_lease_retry_scope(
        request, enabled=retry_execution_lease
    )
    encoded_payload = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    # The execution-lease window begins only after the first exact admitted
    # lease rejection. Pre-admission throttling has its own independent
    # nine-attempt/315-second bound and must not shorten the 1,200-second lease
    # recovery window below the trainer's 960-second stale-owner lifetime.
    lease_deadline: Optional[float] = None
    throttle_failures = 0
    total_invocations = 0

    while True:
        total_invocations += 1
        try:
            response = client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=encoded_payload,
            )
        except ClientError as exc:
            code = _error_code(exc)
            if code not in RETRYABLE_ERROR_CODES:
                raise
            throttle_failures += 1
            if throttle_failures >= max_attempts:
                raise
            delay = _backoff_seconds(throttle_failures)
            print(
                "MLB trainer invoke was throttled before admission; "
                f"retrying throttle attempt {throttle_failures + 1}/"
                f"{max_attempts} in {delay}s ({code})",
                file=sys.stderr,
            )
            if lease_deadline is not None:
                # Keep deterministic admission backoff independent even if a
                # later retry is throttled after contention was first seen.
                lease_deadline += delay
            sleep(delay)
            continue

        response_body = _response_bytes(response)
        response_payload = _parse_json_object(
            response_body, error="lambda_response_json_invalid"
        )
        invocation_metadata = {
            key: response[key]
            for key in (
                "StatusCode",
                "FunctionError",
                "ExecutedVersion",
            )
            if key in response
        }
        function_error = invocation_metadata.get("FunctionError")
        if not function_error:
            return response_body, invocation_metadata

        if not (
            retry_execution_lease
            and response_payload.get("errorType")
            == RETRYABLE_FUNCTION_ERROR_TYPE
        ):
            return response_body, invocation_metadata
        now = monotonic()
        if lease_deadline is None:
            lease_deadline = now + lease_retry_deadline_seconds
        if now + lease_retry_delay_seconds > lease_deadline:
            raise DeployInvokeError(
                "execution_lease_retry_deadline_exceeded"
            )
        print(
            "MLB trainer execution lease is busy; retrying after "
            f"{lease_retry_delay_seconds}s "
            f"(invoke attempt {total_invocations + 1})",
            file=sys.stderr,
        )
        sleep(lease_retry_delay_seconds)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_evidence(*paths: Path) -> None:
    cleanup_error: Optional[BaseException] = None
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            cleanup_error = cleanup_error or exc
    if cleanup_error is not None:
        raise DeployInvokeError("lambda_evidence_cleanup_failed") from cleanup_error


def _request_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    status_sources = (
        args.status_training_result,
        args.status_selection_capture_result,
    )
    if args.payload is not None:
        if any(status_sources):
            raise DeployInvokeError("lambda_request_sources_conflict")
        return _parse_json_object(
            args.payload, error="lambda_request_json_invalid"
        )
    if not all(status_sources):
        raise DeployInvokeError("lambda_request_source_missing")
    return build_status_request(
        _read_json_object(args.status_training_result),
        _read_json_object(args.status_selection_capture_result),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--payload")
    parser.add_argument("--status-training-result", type=Path)
    parser.add_argument("--status-selection-capture-result", type=Path)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--invocation", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
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

    succeeded = False
    try:
        # Clear old evidence before validating request sources so every handled
        # failure is incapable of leaving a prior successful result behind.
        _remove_evidence(args.response, args.invocation)
        request = _request_from_args(args)
        if args.response.resolve() == args.invocation.resolve():
            raise DeployInvokeError("lambda_output_paths_must_differ")
        _validate_execution_lease_retry_scope(
            request, enabled=args.retry_execution_lease
        )
        client = boto3.client(
            "lambda",
            region_name=args.region,
            config=Config(
                connect_timeout=10,
                read_timeout=1000,
                # An SDK retry after a read timeout could duplicate a mutating
                # RequestResponse invocation, so all retries are explicit here.
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        )
        response_body, invocation_metadata = invoke_with_retry(
            client=client,
            function_name=args.function_name,
            payload=json.dumps(request),
            max_attempts=args.max_attempts,
            retry_execution_lease=args.retry_execution_lease,
            lease_retry_deadline_seconds=args.deadline_seconds,
            lease_retry_delay_seconds=args.retry_delay_seconds,
        )
        try:
            _write_atomic(args.response, response_body)
            _write_atomic(
                args.invocation,
                (
                    json.dumps(invocation_metadata, sort_keys=True) + "\n"
                ).encode("utf-8"),
            )
        except Exception as exc:
            raise DeployInvokeError("lambda_evidence_write_failed") from exc
        succeeded = True
    finally:
        if not succeeded:
            # A verifier must never consume stale evidence or one half of a
            # newly written evidence pair after any nonzero exit.
            _remove_evidence(args.response, args.invocation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
