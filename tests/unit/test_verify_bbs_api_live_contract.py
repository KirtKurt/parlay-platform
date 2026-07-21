from __future__ import annotations

import io
import json
import urllib.error

import pytest

from scripts import verify_bbs_api_live_contract as live


API_KEY = "bbs_live_1234567890abcdefghijklmnopqrstuv"


class FakeResponse:
    def __init__(self, payload, *, headers=None, status=200):
        self.payload = payload
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _envelope(data, *, source="official-league"):
    return {
        "data": data,
        "meta": {
            "source": source,
            "confidence": 0.91,
            "cached": False,
            "cache_age_ms": 0,
            "request_id": "req_test",
        },
        "error": None,
    }


def test_verifies_auth_and_mlb_envelope_without_exposing_sensitive_values() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        if request.full_url.endswith("/v1/user/me"):
            return FakeResponse(
                _envelope(
                    {
                        "key_id": "…abcd",
                        "email": "owner@example.com",
                        "plan": "free",
                        "github_connected": True,
                        "paused": False,
                    }
                )
            )
        assert "sport=baseball" in request.full_url
        assert "league=mlb" in request.full_url
        return FakeResponse(
            _envelope(
                [
                    {
                        "match_id": "bb_match_abc123def456",
                        "kickoff_utc": "2026-07-22T23:05:00Z",
                        "sport": "baseball",
                        "home": {"display_name": "New York Yankees"},
                        "away": {"display_name": "Boston Red Sox"},
                        "status": "scheduled",
                    }
                ]
            ),
            headers={"X-RateLimit-Limit": "1000", "X-RateLimit-Remaining": "998"},
        )

    report = live.verify(API_KEY, opener=opener)
    rendered = json.dumps(report)

    assert report["ok"] is True
    assert report["activation"]["mode"] == "SHADOW_ONLY"
    assert report["activation"]["trainingEligibility"] is False
    assert report["activation"]["captureCoverage"] == "PARTIAL_SINGLE_UTC_DATE_PROBE"
    assert report["activation"]["completeSlateCoverageClaimed"] is False
    assert report["activation"]["reviewMilestoneDefined"] is False
    assert report["mlbMatches"]["rowCount"] == 1
    assert report["mlbMatches"]["documentedRowSchemaValidated"] is True
    assert report["mlbMatches"]["providerOfficialGameIdentityDocumented"] is False
    assert report["activation"]["officialIdentityCredit"] is False
    assert report["rateLimit"] == {"limit": "1000", "remaining": "998"}
    assert API_KEY not in rendered
    assert "owner@example.com" not in rendered
    assert "…abcd" not in rendered
    assert len(requests) == 2


def test_rejects_wrong_secret_name_value_shape_before_network() -> None:
    with pytest.raises(live.LiveContractError, match="MISSING_OR_MALFORMED"):
        live.verify("not-a-bbs-key", opener=lambda *_args, **_kwargs: None)


def test_rejects_sandbox_key_before_network() -> None:
    calls = 0

    def opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    with pytest.raises(live.LiveContractError, match="PRODUCTION_LIVE_KEY_REQUIRED"):
        live.verify(
            "bbs_test_1234567890abcdefghijklmnopqrstuv",
            opener=opener,
        )
    assert calls == 0


def test_redacts_authentication_rejection() -> None:
    def opener(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "invalid_api_key",
            {},
            io.BytesIO(b'{"error":{"message":"do not echo request"}}'),
        )

    with pytest.raises(live.LiveContractError) as exc_info:
        live.verify(API_KEY, opener=opener)

    assert str(exc_info.value) == "BBS_AUTH_REJECTED_HTTP_401"
    assert API_KEY not in str(exc_info.value)


def test_rejects_non_array_mlb_matches() -> None:
    calls = 0

    def opener(_request, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse(_envelope({"plan": "free", "paused": False}))
        return FakeResponse(_envelope({"unexpected": "object"}))

    with pytest.raises(live.LiveContractError, match="MATCH_DATA_NOT_ARRAY"):
        live.verify(API_KEY, opener=opener)
