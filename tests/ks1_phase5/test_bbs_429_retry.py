"""BBS 429 gets two retries. A third 429 still fails the capture."""
from io import BytesIO
from urllib.error import HTTPError

import pytest

from ks1.live_inputs import ProviderFailure, fetch


class Scripted:
    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = 0

    def __call__(self, request, timeout=25):
        self.calls += 1
        code = self.codes.pop(0)
        if code != 200:
            raise HTTPError(request.full_url, code, "rate", hdrs={}, fp=BytesIO(b'{"error":{"code":"rate"}}'))

        class Response:
            status = 200
            headers = {}

            def read(self, n):
                return b'{"data":[]}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()


def test_bbs_429_retries_twice():
    opener = Scripted([429, 429, 200])
    sleeps = []
    result = fetch('bbs', 'https://api.bigballsdata.com', '/v1/matches', {'date': '2026-10-03'},
                   key='k', opener=opener, sleeper=sleeps.append)
    assert result['receipt']['status'] == 200
    assert opener.calls == 3
    assert sleeps == [15, 15]


def test_third_bbs_429_still_fails():
    opener = Scripted([429, 429, 429])
    with pytest.raises(ProviderFailure) as exc:
        fetch('bbs', 'https://api.bigballsdata.com', '/v1/matches', {'date': '2026-10-03'},
              key='k', opener=opener, sleeper=lambda _seconds: None)
    assert exc.value.receipt['status'] == 429
    assert opener.calls == 3


def test_odds_429_does_not_retry():
    opener = Scripted([429, 200])
    with pytest.raises(ProviderFailure):
        fetch('odds', 'https://api.the-odds-api.com', '/v4/sports/baseball_mlb/odds', {},
              key='k', opener=opener, sleeper=lambda _seconds: None)
    assert opener.calls == 1
