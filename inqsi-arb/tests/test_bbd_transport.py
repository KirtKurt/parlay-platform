"""Exercise urllib's response processing offline, including credential redirects."""
import io
import sys
from email.message import Message
from pathlib import Path
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import bbd_provider


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "offline-test-token")
    monkeypatch.setenv("BBD_BASE_URL", "https://bbd.invalid")
    calls = []
    responses = []

    def install(code, location=None, body=b'{"data": []}'):
        class OfflineHTTPS(HTTPSHandler):
            def https_open(self, request):
                calls.append(request)
                headers = Message()
                if location is not None:
                    headers["Location"] = location
                response = addinfourl(io.BytesIO(body), headers, request.full_url, code)
                response.msg = "offline fixture"
                responses.append(response)
                return response

        monkeypatch.setattr(
            bbd_provider, "build_opener",
            lambda *handlers: build_opener(*handlers, OfflineHTTPS()),
        )

    return install, calls, responses


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("location", [
    "https://other.invalid/collect", "https://bbd.invalid/moved",
    "/relative", "http://other.invalid/collect", None,
])
@pytest.mark.parametrize("operation", ["health", "sports", "events"])
def test_redirects_fail_closed_without_second_request(transport, code, location, operation):
    install, calls, responses = transport
    install(code, location)

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_REDIRECT_NOT_ALLOWED"
    assert len(calls) == 1
    assert calls[0].host == "bbd.invalid"
    assert responses[0].closed
    assert "offline-test-token" not in str(result)
    if operation != "health":
        assert result[operation] == []


@pytest.mark.parametrize("body", [
    b'{"data": [{"id": "\xff"}]}',
    b'{"data": []}\xc3',
    b'{"data": [',
    b'<html>upstream error</html>',
])
@pytest.mark.parametrize("operation", ["health", "sports", "events"])
def test_invalid_response_body_fails_closed(transport, body, operation):
    install, calls, responses = transport
    install(200, body=body)

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_REQUEST_FAILED"
    assert len(calls) == 1
    assert responses[0].closed
    assert "offline-test-token" not in str(result)
    assert "upstream error" not in str(result)
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


def test_direct_request_preserves_auth_and_query_contract(transport):
    install, calls, responses = transport
    install(200)

    result = bbd_provider.events(sport="ice hockey", league="test/league")

    assert result["ok"] is True
    assert result["events"] == []
    assert len(calls) == 1
    assert calls[0].full_url == "https://bbd.invalid/v1/matches?sport=ice+hockey&league=test%2Fleague"
    assert calls[0].get_header("Authorization") == "Bearer offline-test-token"
    assert calls[0].get_header("Accept") == "application/json"
    assert responses[0].closed
