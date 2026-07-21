from __future__ import annotations

import io
import json
import urllib.error

import pytest

from hello_world import bigballsdata_client as bbs


KEY = "bbs_test_1234567890abcdefghijklmnopqrstuv"


class Secrets:
    def __init__(self, value=KEY, *, fail=False):
        self.value = value
        self.fail = fail
        self.calls = []

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("sensitive SDK details")
        return {"SecretString": self.value}


class Response:
    status = 200

    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def envelope(data):
    return {
        "data": data,
        "meta": {
            "source": "official-league",
            "confidence": 0.8,
            "cached": False,
            "cache_age_ms": 0,
            "request_id": "req-test",
        },
        "error": None,
    }


def test_resolves_scoped_secrets_manager_credential(monkeypatch):
    monkeypatch.delenv("BBS_API_KEY", raising=False)
    secrets = Secrets()

    value = bbs.resolve_api_key(secret_arn="arn:aws:secretsmanager:test", secrets_client=secrets)

    assert value == KEY
    assert secrets.calls == [{"SecretId": "arn:aws:secretsmanager:test"}]


def test_secret_sdk_failure_is_redacted(monkeypatch):
    monkeypatch.delenv("BBS_API_KEY", raising=False)
    with pytest.raises(bbs.BBSClientError) as exc_info:
        bbs.resolve_api_key(secret_arn="arn:test", secrets_client=Secrets(fail=True))
    assert str(exc_info.value) == "BBS_SECRET_RETRIEVAL_FAILED"


def test_lists_mlb_matches_with_exact_filters_and_no_key_in_repr():
    seen = []

    def opener(request, *, timeout):
        seen.append((request, timeout))
        return Response(envelope([]), {"X-RateLimit-Remaining": "999"})

    client = bbs.BigBallsDataClient(api_key=KEY, opener=opener, sleeper=lambda _: None)
    result = client.list_mlb_matches("2026-07-22")

    assert result["data"] == []
    request, timeout = seen[0]
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert "sport=baseball" in request.full_url
    assert "league=mlb" in request.full_url
    assert "date=2026-07-22" in request.full_url
    assert timeout == 4
    assert KEY not in repr(client)


def test_authentication_error_never_includes_upstream_body_or_key():
    def opener(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "invalid",
            {},
            io.BytesIO(f'{{"echo":"{KEY}"}}'.encode()),
        )

    client = bbs.BigBallsDataClient(api_key=KEY, opener=opener, sleeper=lambda _: None)
    with pytest.raises(bbs.BBSAuthenticationError) as exc_info:
        client.list_mlb_matches("2026-07-22")
    assert str(exc_info.value) == "BBS_AUTH_REJECTED_HTTP_401"
    assert KEY not in str(exc_info.value)


def test_transient_503_retries_then_succeeds():
    calls = 0
    sleeps = []

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b""))
        return Response(envelope([]))

    client = bbs.BigBallsDataClient(
        api_key=KEY,
        opener=opener,
        sleeper=sleeps.append,
        max_attempts=2,
    )

    assert client.list_mlb_matches("2026-07-22")["data"] == []
    assert calls == 2
    assert len(sleeps) == 1


def test_default_503_is_one_short_fail_soft_attempt():
    calls = 0
    sleeps = []

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 4
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b""))

    client = bbs.BigBallsDataClient(
        api_key=KEY,
        opener=opener,
        sleeper=sleeps.append,
    )

    with pytest.raises(bbs.BBSTransientError, match="BBS_UPSTREAM_HTTP_503"):
        client.list_mlb_matches("2026-07-22")
    assert calls == 1
    assert sleeps == []


def test_429_never_retries_even_when_caller_requests_attempts():
    calls = 0
    sleeps = []

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "quota",
            {"Retry-After": "600"},
            io.BytesIO(b""),
        )

    client = bbs.BigBallsDataClient(
        api_key=KEY,
        opener=opener,
        sleeper=sleeps.append,
        max_attempts=3,
    )

    with pytest.raises(bbs.BBSTransientError, match="BBS_RATE_LIMITED"):
        client.list_mlb_matches("2026-07-22")
    assert calls == 1
    assert sleeps == []
