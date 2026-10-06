"""Bounded BBS 429 retries. Capture still fails if the quota does not clear."""
import io
from urllib.error import HTTPError

from ks1.live_inputs import ProviderFailure, fetch


def _rate_limited(request):
    return HTTPError(
        request.full_url, 429, "rate", hdrs={},
        fp=io.BytesIO(b'{"error":{"code":"rate","message":"limited","retryable":true}}'))


class _Ok:
    status = 200
    headers = {}

    def read(self, _n):
        return b'{"data":[]}'

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_bbs_429_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def opener(request, timeout=25):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _rate_limited(request)
        return _Ok()

    monkeypatch.setattr("ks1.live_inputs.time.sleep", lambda _seconds: None)
    result = fetch(
        "bbs", "https://api.bigballsdata.com", "/v1/matches",
        {"sport": "baseball"}, key="test-key", opener=opener)
    assert calls["n"] == 3
    assert result["receipt"]["status"] == 200
    assert result["payload"] == {"data": []}


def test_bbs_429_remains_hard_error_after_budget(monkeypatch):
    calls = {"n": 0}

    def opener(request, timeout=25):
        calls["n"] += 1
        raise _rate_limited(request)

    monkeypatch.setattr("ks1.live_inputs.time.sleep", lambda _seconds: None)
    try:
        fetch(
            "bbs", "https://api.bigballsdata.com", "/v1/matches",
            {"sport": "baseball"}, key="test-key", opener=opener)
    except ProviderFailure as exc:
        assert exc.receipt["status"] == 429
        assert calls["n"] == 4
    else:
        raise AssertionError("429 must remain a hard capture error")
