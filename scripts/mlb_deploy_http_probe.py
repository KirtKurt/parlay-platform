#!/usr/bin/env python3
"""Bounded, sequential JSON probes for capacity-constrained MLB deploy checks."""

from __future__ import annotations

import argparse
import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Optional


TRANSIENT_HTTP_STATUSES = {429, *range(500, 600)}
TRANSIENT_PROBE_RESULT_VERSION = "MLB-DEPLOY-HTTP-PROBE-v2-outer-poll-safe"


class HttpProbeError(RuntimeError):
    """Base deploy-probe failure."""


class PermanentHttpProbeError(HttpProbeError):
    """A response that retrying cannot make contract-valid."""


class TransientHttpProbeExhausted(HttpProbeError):
    """Transient delivery failures consumed the bounded probe deadline."""


@dataclass(frozen=True)
class JsonProbeResponse:
    """One accepted JSON response, including its actual HTTP status."""

    http_status: Optional[int]
    payload: dict[str, Any]


def _transient_probe_result(attempts: int, reason: str) -> dict[str, Any]:
    """Return a fail-closed object that an outer lifecycle poll can retry.

    The result intentionally cannot satisfy any production acceptance contract:
    ``ok`` is false and no domain fields are present.  It exists only so a
    caller that owns a longer shared deadline can perform one sequential HTTP
    delivery per outer attempt without one timeout consuming that whole window.
    """

    return {
        "ok": False,
        "transientProbe": True,
        "retryable": True,
        "probeVersion": TRANSIENT_PROBE_RESULT_VERSION,
        "attempts": int(attempts),
        "reason": str(reason or "transient delivery failure"),
    }


def fetch_json_response(
    url: str,
    *,
    deadline_monotonic: Optional[float] = None,
    max_wait_seconds: float = 180.0,
    request_timeout_seconds: float = 20.0,
    retry_delay_seconds: float = 4.0,
    max_attempts: Optional[int] = None,
    headers: Optional[Mapping[str, str]] = None,
    accepted_http_statuses: Collection[int] = (200,),
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> JsonProbeResponse:
    """Fetch one JSON object and retain the accepted HTTP status.

    Calls are strictly sequential. HTTP 429, HTTP 5xx, transport errors, and
    truncated/invalid JSON are retryable unless the caller explicitly accepts
    the HTTP status and its response body is a valid JSON object.
    ``max_attempts`` always bounds the deliveries made by this function call.

    Explicit status acceptance is required for domain-level fail-closed
    responses such as MLB's intentional ``503 NO_QUALIFIED_CHAMPION``. The
    actual status is returned so the caller can verify the status and body as
    one atomic contract; an arbitrary 503 can never be mistaken for success.

    When the caller supplies an explicit shared deadline and the local attempt
    cap is reached first, the function returns a fail-closed transient object
    instead of raising.  This lets the caller's outer lifecycle loop sleep,
    refresh its state, and retry one delivery at a time.  A shared deadline that
    is already exhausted still raises immediately.  Non-retryable HTTP statuses
    and valid non-object payloads always fail immediately because they indicate
    a deployment contract error.
    """

    if not url:
        raise PermanentHttpProbeError("probe URL is empty")
    if (
        request_timeout_seconds <= 0
        or retry_delay_seconds < 0
        or (max_attempts is not None and max_attempts <= 0)
    ):
        raise ValueError(
            "probe timeout/max attempts must be positive and retry delay "
            "must be non-negative"
        )
    accepted_statuses = {int(status) for status in accepted_http_statuses}
    if not accepted_statuses:
        raise ValueError("accepted HTTP statuses must not be empty")
    shared_deadline = deadline_monotonic is not None
    deadline = (
        float(deadline_monotonic)
        if shared_deadline
        else monotonic() + max(0.0, float(max_wait_seconds))
    )
    request_headers = {
        "accept": "application/json",
        "user-agent": "inqsi-capacity-safe-deploy-probe/2.0",
        **dict(headers or {}),
    }
    last_transient = "transient delivery failure"
    last_http_status: Optional[int] = None
    attempt = 0

    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TransientHttpProbeExhausted(
                f"JSON probe deadline exhausted after {attempt} attempts: "
                f"{last_transient}"
            )
        attempt += 1
        timeout = max(
            0.1,
            min(
                float(request_timeout_seconds),
                remaining if remaining > 0 else float(request_timeout_seconds),
            ),
        )
        request = urllib.request.Request(
            url,
            headers=request_headers,
            method="GET",
        )
        try:
            with opener(request, timeout=timeout) as response:
                status = int(response.getcode())
                last_http_status = status
                if status not in accepted_statuses:
                    if status in TRANSIENT_HTTP_STATUSES:
                        raise urllib.error.HTTPError(
                            url,
                            status,
                            "transient response",
                            response.headers,
                            None,
                        )
                    raise PermanentHttpProbeError(
                        f"JSON probe returned non-retryable HTTP {status}"
                    )
                raw = response.read()
                payload = json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            last_http_status = status
            if status in accepted_statuses:
                try:
                    raw = exc.read()
                    payload = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as decode_exc:
                    last_transient = type(decode_exc).__name__
                else:
                    if not isinstance(payload, dict):
                        raise PermanentHttpProbeError(
                            "JSON probe returned a valid non-object payload"
                        )
                    return JsonProbeResponse(status, payload)
            elif status not in TRANSIENT_HTTP_STATUSES:
                raise PermanentHttpProbeError(
                    f"JSON probe returned non-retryable HTTP {status}"
                ) from exc
            else:
                last_transient = f"HTTP {status}"
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.IncompleteRead,
        ) as exc:
            last_transient = type(exc).__name__
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_transient = type(exc).__name__
        else:
            if not isinstance(payload, dict):
                raise PermanentHttpProbeError(
                    "JSON probe returned a valid non-object payload"
                )
            return JsonProbeResponse(status, payload)

        if max_attempts is not None and attempt >= max_attempts:
            if shared_deadline:
                return JsonProbeResponse(
                    last_http_status,
                    _transient_probe_result(attempt, last_transient),
                )
            raise TransientHttpProbeExhausted(
                f"JSON probe attempt limit exhausted after {attempt} attempts: "
                f"{last_transient}"
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TransientHttpProbeExhausted(
                f"JSON probe deadline exhausted after {attempt} attempts: "
                f"{last_transient}"
            )
        sleep(min(float(retry_delay_seconds), remaining))


def fetch_json_object(
    url: str,
    *,
    deadline_monotonic: Optional[float] = None,
    max_wait_seconds: float = 180.0,
    request_timeout_seconds: float = 20.0,
    retry_delay_seconds: float = 4.0,
    max_attempts: Optional[int] = None,
    headers: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch an HTTP-200 JSON object with bounded transient retries."""

    return fetch_json_response(
        url,
        deadline_monotonic=deadline_monotonic,
        max_wait_seconds=max_wait_seconds,
        request_timeout_seconds=request_timeout_seconds,
        retry_delay_seconds=retry_delay_seconds,
        max_attempts=max_attempts,
        headers=headers,
        accepted_http_statuses=(200,),
        opener=opener,
        monotonic=monotonic,
        sleep=sleep,
    ).payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-wait-seconds", type=float, default=180.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    args = parser.parse_args()

    payload = fetch_json_object(
        args.url,
        max_wait_seconds=args.max_wait_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    Path(args.output).write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
