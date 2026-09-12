"""Exercise urllib's response processing offline, including credential redirects."""
import io
import sys
from email.message import Message
from pathlib import Path
from urllib.request import HTTPSHandler, ProxyHandler, build_opener
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
            # The fake transport must not inherit the runner proxy or mutate
            # Request.host before OfflineHTTPS records it. No network is used.
            lambda *handlers: build_opener(ProxyHandler({}), *handlers, OfflineHTTPS()),
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


@pytest.mark.parametrize("body", [
    b'{"data": [], "data": [{"id": "hidden"}]}',
    b'{"data": [{"id": "first", "id": "second"}]}',
    b'{"data": [{"status": "scheduled", "status": "finished"}]}',
    b'{"data": [{"id": "first", "\\u0069d": "second"}]}',
    b'{"data": [{"home": {"id": "first", "id": "second"}}]}',
    b'{"data": [{"id": NaN}]}',
    b'{"data": [{"start_time": Infinity}]}',
    b'{"data": [{"start_time": -Infinity}]}',
])
@pytest.mark.parametrize("operation", ["health", "sports", "events"])
def test_ambiguous_or_nonstandard_json_fails_closed(transport, body, operation):
    install, calls, responses = transport
    install(200, body=body)

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_RESPONSE_JSON_INVALID"
    assert len(calls) == 1
    assert responses[0].closed
    assert "offline-test-token" not in str(result)
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


def test_valid_json_preserves_separate_objects_and_string_constants(transport):
    install, _, responses = transport
    install(200, body=b'{"data": [{"id": "one", "status": "NaN"}, '
                      b'{"id": "two", "status": "Infinity"}]}')

    result = bbd_provider.events()

    assert result["ok"] is True
    assert [event["bbd_event_id"] for event in result["events"]] == ["one", "two"]
    assert [event["status"] for event in result["events"]] == ["NaN", "Infinity"]
    assert responses[0].closed


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


@pytest.mark.parametrize("base_url", [
    "http://bbd.invalid", "ftp://bbd.invalid", "//bbd.invalid", "https:///missing-host",
    "https://user:password@bbd.invalid", "https://user@bbd.invalid",
    "https://bbd.invalid?token=secret", "https://bbd.invalid#fragment",
    "https://bbd.invalid?", "https://bbd.invalid#",
    "https://bbd.invalid:bad", "https://bbd.invalid:65536", "https://bbd.invalid:0",
    "https://[broken", " https://bbd.invalid", "https://bbd.invalid\n",
    "https://bbd.invalid/with space", "https://bbd.invalid/\x01",
    "https://bbd.invalid/\x7f", "https://bbd.invalid\\other",
])
@pytest.mark.parametrize("operation", ["health", "sports", "events"])
def test_unsafe_base_url_fails_before_transport(monkeypatch, transport, base_url, operation):
    monkeypatch.setenv("BBD_BASE_URL", base_url)

    def unexpected_opener(*args):
        pytest.fail("Unsafe base URL must be rejected before opening transport")

    monkeypatch.setattr(bbd_provider, "build_opener", unexpected_opener)
    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_BASE_URL_INVALID"
    assert "offline-test-token" not in str(result)
    if operation != "health":
        assert result[operation] == []


@pytest.mark.parametrize("base_url", [
    "https://bbd.invalid", "https://bbd.invalid/", "https://bbd.invalid:8443/context/",
])
def test_valid_https_base_urls_preserve_endpoint(transport, monkeypatch, base_url):
    install, calls, _ = transport
    install(200)
    monkeypatch.setenv("BBD_BASE_URL", base_url)

    result = bbd_provider.events()

    assert result["ok"] is True
    assert calls[0].full_url == base_url.rstrip("/") + "/v1/matches"
    assert calls[0].get_header("Authorization") == "Bearer offline-test-token"


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("authority", [
    "%00", ".", "%2e", "%62bd.invalid", "bbd%ZZ.invalid",
    "bad..invalid", "-bad.invalid", "bad-.invalid", "bad_name.invalid",
    "a" * 64 + ".invalid", ".".join(["a" * 63] * 4),
    "999.1.1.1", "127.1", "127.0.0.01", "bbd.invalid:",
    "[::1]garbage", "[fe80::1%25eth0]", "é.invalid", "\udcff.invalid",
])
def test_malformed_authority_rejected_before_authenticated_request(
    monkeypatch, transport, authority, operation,
):
    monkeypatch.setenv("BBD_BASE_URL", "https://" + authority)

    def unexpected_request(*args, **kwargs):
        pytest.fail("Malformed authority must be rejected before constructing a request")

    monkeypatch.setattr(bbd_provider, "Request", unexpected_request)
    result = getattr(bbd_provider, operation)()
    assert result["ok"] is False
    assert result["reason"] == "BBD_BASE_URL_INVALID"
    assert "offline-test-token" not in str(result)
    if operation != "health":
        assert result[operation] == []


@pytest.mark.parametrize("base_url", [
    "https://127.0.0.1:8443/context", "https://[::1]:8443/context",
    "https://[2001:db8::1]", "https://bbd.invalid.",
    "https://xn--bcher-kva.invalid", "https://BBD.INVALID:443/context",
])
def test_valid_host_syntax_preserves_destination(monkeypatch, transport, base_url):
    install, calls, _ = transport
    monkeypatch.setenv("BBD_BASE_URL", base_url)
    install(200)
    assert bbd_provider.sports()["ok"] is True
    assert calls[0].full_url == base_url + "/v1/sports"
