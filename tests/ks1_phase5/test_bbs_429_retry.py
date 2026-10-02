"""BBS 429 retries once. A second 429 remains a hard capture error."""
import io
import json
from email.message import Message
from urllib.error import HTTPError

from ks1.live_inputs import ProviderFailure, fetch


class _Resp:
    def __init__(self, body):
        self._body = body
        self.status = 200
        self.headers = {}

    def read(self, n):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(request, code, payload):
    headers = Message()
    return HTTPError(request.full_url, code, "rate", headers, io.BytesIO(payload))


def test_bbs_429_retries_once_then_succeeds(monkeypatch):
    calls = {"n": 0}
    body = json.dumps({"data": []}).encode()

    def opener(request, timeout=25):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(request, 429, b'{"error":{"code":"rate","retryable":true}}')
        return _Resp(body)

    monkeypatch.setattr("ks1.live_inputs.time.sleep", lambda seconds: None)
    result = fetch("bbs", "https://api.bigballsdata.com", "/v1/matches",
                   {"sport": "baseball"}, key="test-key", opener=opener)
    assert calls["n"] == 2
    assert result["payload"] == {"data": []}
    assert result["receipt"]["status"] == 200


def test_second_bbs_429_stays_hard(monkeypatch):
    def opener(request, timeout=25):
        raise _http_error(request, 429, b'{"error":{"code":"rate","retryable":true}}')

    monkeypatch.setattr("ks1.live_inputs.time.sleep", lambda seconds: None)
    try:
        fetch("bbs", "https://api.bigballsdata.com", "/v1/matches",
              {"sport": "baseball"}, key="test-key", opener=opener)
    except ProviderFailure as exc:
        assert exc.receipt["status"] == 429
        assert exc.receipt["provider"] == "bbs"
    else:
        raise AssertionError("second 429 must fail closed")
