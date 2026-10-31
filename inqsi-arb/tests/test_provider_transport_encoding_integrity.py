"""Provider transport must fail closed on malformed response encodings and JSON."""

import http.client
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


class _Response:
    status = 200
    headers = {}

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body

    def getcode(self):
        return self.status


def _fetch(monkeypatch, body):
    monkeypatch.setattr(
        provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(body),
    )
    return provider._get("https://example.invalid/sports/", {"apiKey": "dummy"})


def test_invalid_utf8_provider_response_is_fail_closed_not_uncaught(monkeypatch):
    payload, meta = _fetch(monkeypatch, b"\xff\xfe")
    assert payload is None
    assert meta == {"ok": False, "error": "PROVIDER_UNAVAILABLE"}


def test_truncated_json_provider_response_is_fail_closed(monkeypatch):
    payload, meta = _fetch(monkeypatch, b'{"sports":')
    assert payload is None
    assert meta == {"ok": False, "error": "PROVIDER_UNAVAILABLE"}


def test_valid_utf8_json_transport_remains_accepted(monkeypatch):
    payload, meta = _fetch(monkeypatch, b'[]')
    assert payload == []
    assert meta["ok"] is True
    assert meta["status"] == 200


def test_incomplete_http_body_is_fail_closed_not_uncaught(monkeypatch):
    class _TruncatedResponse(_Response):
        def read(self):
            raise http.client.IncompleteRead(b'{"sports":', 12)

    monkeypatch.setattr(
        provider.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _TruncatedResponse(b""),
    )
    payload, meta = provider._get("https://example.invalid/sports/", {"apiKey": "dummy"})
    assert payload is None
    assert meta == {"ok": False, "error": "PROVIDER_UNAVAILABLE"}


def test_invalid_http_status_line_is_fail_closed_not_uncaught(monkeypatch):
    def invalid_status(*_args, **_kwargs):
        raise http.client.BadStatusLine("BROKEN STATUS")

    monkeypatch.setattr(provider.urllib.request, "urlopen", invalid_status)
    payload, meta = provider._get("https://example.invalid/sports/", {"apiKey": "dummy"})
    assert payload is None
    assert meta == {"ok": False, "error": "PROVIDER_UNAVAILABLE"}
