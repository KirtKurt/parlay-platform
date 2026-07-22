from __future__ import annotations

import io
import http.client
import json
import urllib.error

import pytest

from scripts import mlb_deploy_http_probe as probe


class Clock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class Response:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


def test_retries_transient_capacity_response_sequentially() -> None:
    clock = Clock()
    outcomes = [
        urllib.error.HTTPError("https://example.test", 500, "busy", {}, io.BytesIO()),
        urllib.error.HTTPError("https://example.test", 429, "busy", {}, io.BytesIO()),
        Response(200, {"ok": True}),
    ]
    in_flight = 0
    max_in_flight = 0

    def opener(_request, *, timeout):
        nonlocal in_flight, max_in_flight
        assert timeout > 0
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        outcome = outcomes.pop(0)
        in_flight -= 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    result = probe.fetch_json_object(
        "https://example.test/status",
        max_wait_seconds=30,
        retry_delay_seconds=4,
        opener=opener,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result == {"ok": True}
    assert clock.sleeps == [4, 4]
    assert max_in_flight == 1


@pytest.mark.parametrize("status", (400, 401, 403, 404))
def test_non_retryable_http_status_fails_immediately(status: int) -> None:
    clock = Clock()

    def opener(_request, *, timeout):
        raise urllib.error.HTTPError(
            "https://example.test",
            status,
            "contract failure",
            {},
            io.BytesIO(),
        )

    with pytest.raises(probe.PermanentHttpProbeError, match=str(status)):
        probe.fetch_json_object(
            "https://example.test/status",
            opener=opener,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert clock.sleeps == []


def test_transient_network_failure_stops_at_deadline() -> None:
    clock = Clock()

    def opener(_request, *, timeout):
        clock.value += timeout
        raise TimeoutError("capacity-starved")

    with pytest.raises(probe.TransientHttpProbeExhausted, match="deadline"):
        probe.fetch_json_object(
            "https://example.test/status",
            max_wait_seconds=9,
            request_timeout_seconds=5,
            retry_delay_seconds=2,
            opener=opener,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert sum(clock.sleeps) <= 4


def test_truncated_response_body_is_retried() -> None:
    clock = Clock()
    outcomes = [
        http.client.IncompleteRead(b'{"ok":', 8),
        Response(200, {"ok": True}),
    ]

    class TruncatedResponse(Response):
        def read(self) -> bytes:
            raise outcomes.pop(0)

    calls = 0

    def opener(_request, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return TruncatedResponse(200, {})
        return outcomes.pop(0)

    result = probe.fetch_json_object(
        "https://example.test/status",
        max_wait_seconds=20,
        opener=opener,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result == {"ok": True}
    assert calls == 2
    assert clock.sleeps == [4]


def test_valid_non_object_payload_is_a_contract_failure() -> None:
    clock = Clock()

    with pytest.raises(probe.PermanentHttpProbeError, match="non-object"):
        probe.fetch_json_object(
            "https://example.test/status",
            opener=lambda _request, timeout: Response(200, []),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert clock.sleeps == []


def test_expired_shared_deadline_makes_no_request() -> None:
    clock = Clock()
    calls = 0

    def opener(_request, *, timeout):
        nonlocal calls
        calls += 1
        return Response(200, {"ok": True})

    with pytest.raises(probe.TransientHttpProbeExhausted, match="deadline"):
        probe.fetch_json_object(
            "https://example.test/status",
            deadline_monotonic=clock.monotonic(),
            opener=opener,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert calls == 0


@pytest.mark.parametrize("failure", ("timeout", "http_504", "truncated"))
def test_single_attempt_heavy_probe_never_retries_failed_delivery(
    failure: str,
) -> None:
    clock = Clock()
    calls = 0

    class TruncatedResponse(Response):
        def read(self) -> bytes:
            raise http.client.IncompleteRead(b'{"ok":', 8)

    def opener(_request, *, timeout):
        nonlocal calls
        calls += 1
        assert timeout > 0
        if failure == "timeout":
            raise TimeoutError("gateway integration still running")
        if failure == "http_504":
            raise urllib.error.HTTPError(
                "https://example.test",
                504,
                "gateway timeout",
                {},
                io.BytesIO(),
            )
        return TruncatedResponse(200, {})

    with pytest.raises(
        probe.TransientHttpProbeExhausted,
        match="attempt limit exhausted after 1 attempts",
    ):
        probe.fetch_json_object(
            "https://example.test/status",
            max_wait_seconds=1200,
            request_timeout_seconds=45,
            retry_delay_seconds=4,
            max_attempts=1,
            opener=opener,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert calls == 1
    assert clock.sleeps == []


def test_single_attempt_heavy_probe_accepts_valid_json_object() -> None:
    clock = Clock()
    result = probe.fetch_json_object(
        "https://example.test/status",
        max_attempts=1,
        opener=lambda _request, timeout: Response(200, {"ok": True}),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result == {"ok": True}
    assert clock.sleeps == []
