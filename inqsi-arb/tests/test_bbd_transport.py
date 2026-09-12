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


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 503])
@pytest.mark.parametrize("operation", ["health", "sports", "events"])
def test_http_errors_close_responses_and_preserve_status(transport, code, operation):
    install, calls, responses = transport
    install(code, body=b'offline-test-token: provider diagnostic')

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    optional_access_failure = code in {401, 403, 404}
    if optional_access_failure and operation == "health":
        assert result["reason"] == "BBD_AUTH_OR_DISCOVERY_FAILED"
        assert result["auth_status"] == result["sports_status"] == code
    elif optional_access_failure and operation == "events":
        assert result["reason"] == "BBD_MATCH_ENDPOINT_UNAVAILABLE_OR_UNENTITLED"
        assert result["status"] == code
    else:
        assert result["reason"] == f"BBD_HTTP_{code}"
    assert len(calls) == (2 if optional_access_failure and operation == "health" else 1)
    assert all(response.closed for response in responses)
    assert "offline-test-token" not in str(result)
    assert "provider diagnostic" not in str(result)
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("code", [401, 403, 404, 429, 503])
def test_http_error_bodies_are_never_read(monkeypatch, transport, optional, code):
    from urllib.error import HTTPError

    class UnreadableBody(io.BytesIO):
        def read(self, *args, **kwargs):
            pytest.fail("HTTP error bodies must not be read")

    body = UnreadableBody(b'offline-test-token: provider diagnostic')
    error = HTTPError("https://bbd.invalid/v1/sports", code, "offline fixture", Message(), body)

    class ErrorOpener:
        def open(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(bbd_provider, "build_opener", lambda *args: ErrorOpener())
    if optional and code in {401, 403, 404}:
        assert bbd_provider._request("/v1/sports", optional=optional) == (code, {}, {})
    else:
        with pytest.raises(bbd_provider.BBDError) as raised:
            bbd_provider._request("/v1/sports", optional=optional)
        assert str(raised.value) == f"BBD_HTTP_{code}"
    assert body.closed
