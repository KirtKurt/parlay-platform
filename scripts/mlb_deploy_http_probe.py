#!/usr/bin/env python3
"""Bounded, sequential JSON probes for capacity-constrained MLB deploy checks."""

from __future__ import annotations

import argparse
import http.client
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


TRANSIENT_HTTP_STATUSES = {429, *range(500, 600)}


class HttpProbeError(RuntimeError):
    """Base deploy-probe failure."""


class PermanentHttpProbeError(HttpProbeError):
    """A response that retrying cannot make contract-valid."""


class TransientHttpProbeExhausted(HttpProbeError):
    """Transient delivery failures consumed the bounded probe deadline."""


def fetch_json_object(
    url: str,
    *,
    deadline_monotonic: Optional[float] = None,
    max_wait_seconds: float = 180.0,
    request_timeout_seconds: float = 20.0,
    retry_delay_seconds: float = 4.0,
    headers: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch one JSON object, retrying only capacity/network-class failures.

    Calls are strictly sequential. HTTP 429, HTTP 5xx, transport errors, and
    truncated/invalid JSON are retried only until the supplied monotonic
    deadline. Other HTTP statuses and valid non-object payloads fail
    immediately because they indicate a deployment contract error.
    """

    if not url:
        raise PermanentHttpProbeError("probe URL is empty")
    if request_timeout_seconds <= 0 or retry_delay_seconds < 0:
        raise ValueError("probe timeout and retry delay must be non-negative")
    deadline = (
        float(deadline_monotonic)
        if deadline_monotonic is not None
        else monotonic() + max(0.0, float(max_wait_seconds))
    )
    request_headers = {
        "accept": "application/json",
        "user-agent": "inqsi-capacity-safe-deploy-probe/1.0",
        **dict(headers or {}),
    }
    last_transient = "transient delivery failure"
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
                if status != 200:
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
            if int(exc.code) not in TRANSIENT_HTTP_STATUSES:
                raise PermanentHttpProbeError(
                    f"JSON probe returned non-retryable HTTP {exc.code}"
                ) from exc
            last_transient = f"HTTP {exc.code}"
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
            return payload

        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TransientHttpProbeExhausted(
                f"JSON probe deadline exhausted after {attempt} attempts: "
                f"{last_transient}"
            )
        sleep(min(float(retry_delay_seconds), remaining))


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
