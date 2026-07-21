#!/usr/bin/env python3
"""Invoke the AWS MLB trainer with bounded, explicitly safe contention retries."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


RETRYABLE_ERROR_CODES = frozenset({"TooManyRequestsException"})
DEFAULT_MAX_ATTEMPTS = 9
MAX_BACKOFF_SECONDS = 60


def _error_code(exc: BaseException) -> str:
    if not isinstance(exc, ClientError):
        return ""
    return str(((exc.response or {}).get("Error") or {}).get("Code") or "")


def _backoff_seconds(failed_attempt: int) -> int:
    return min(MAX_BACKOFF_SECONDS, 5 * (2 ** max(0, failed_attempt - 1)))


def invoke_with_retry(
    *,
    client: Any,
    function_name: str,
    payload: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[bytes, Dict[str, Any]]:
    if not 1 <= max_attempts <= DEFAULT_MAX_ATTEMPTS:
        raise ValueError(
            f"max_attempts must be between 1 and {DEFAULT_MAX_ATTEMPTS}"
        )
    encoded_payload = payload.encode("utf-8")
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.invoke(
                FunctionName=function_name,
                InvocationType="RequestResponse",
                Payload=encoded_payload,
            )
        except ClientError as exc:
            code = _error_code(exc)
            if code not in RETRYABLE_ERROR_CODES or attempt >= max_attempts:
                raise
            delay = _backoff_seconds(attempt)
            print(
                "MLB trainer invoke was throttled before admission; "
                f"retrying attempt {attempt + 1}/{max_attempts} in {delay}s "
                f"({code})",
                file=sys.stderr,
            )
            sleep(delay)
            continue

        payload_stream = response.get("Payload")
        if payload_stream is None or not hasattr(payload_stream, "read"):
            raise RuntimeError("Lambda invoke response payload stream is missing")
        response_body = payload_stream.read()
        if not isinstance(response_body, bytes):
            raise RuntimeError("Lambda invoke response payload is not bytes")
        invocation_metadata = {
            key: response[key]
            for key in ("StatusCode", "FunctionError", "LogResult", "ExecutedVersion")
            if key in response
        }
        return response_body, invocation_metadata
    raise AssertionError("unreachable invoke retry state")


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--function-name", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--invocation", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    args = parser.parse_args()

    # A failed retry sequence must not leave stale evidence that a later step
    # could mistake for this invocation. The deploy runner normally starts
    # clean, but removing the canonical outputs makes that property explicit.
    args.response.unlink(missing_ok=True)
    args.invocation.unlink(missing_ok=True)

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
        payload=args.payload,
        max_attempts=args.max_attempts,
    )
    _write_atomic(args.response, response_body)
    _write_atomic(
        args.invocation,
        (json.dumps(invocation_metadata, sort_keys=True) + "\n").encode("utf-8"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
