"""BBS 429 is a missing catalogue, not a slate-killing identity error."""
import json
from urllib.error import HTTPError
from io import BytesIO

import pytest

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure, bbs_catalogue, fetch


def test_429_receipt_degrades_to_empty_catalogue():
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {'error': 'dict'}}
    assert bbs_unavailable(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded['payload'] == {'data': []}
    assert degraded['receipt']['degraded'] == 'bbs_unavailable'
    assert degraded['receipt']['match_catalogue'] == 'empty'


def test_truncation_and_schema_stay_hard_errors():
    assert not bbs_unavailable({'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'})
    with pytest.raises(ValueError):
        degraded_bbs_capture({'provider': 'bbs', 'status': 200})
    def requester(provider, base, path, params, key=None):
        return {'payload': {'data': [{'id': 'x'}] * 200},
                'receipt': {'provider': 'bbs', 'status': 200, 'as_of': '2026-10-07T00:00:00+00:00'}}
    with pytest.raises(ProviderFailure) as exc:
        bbs_catalogue('2026-10-07', [], 'key', requester=requester)
    assert exc.value.receipt['error'] == 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'


def test_fetch_retries_429_then_raises(monkeypatch):
    sleeps = []
    monkeypatch.setattr('ks1.live_inputs.time.sleep', lambda s: sleeps.append(s))

    class Response:
        def __init__(self):
            self.code = 429
        def read(self, n):
            return json.dumps({'error': {'code': 'rate'}}).encode()

    def opener(request, timeout=25):
        raise HTTPError(request.full_url, 429, 'Too Many Requests', hdrs=None, fp=BytesIO(b'{}'))

    with pytest.raises(ProviderFailure) as exc:
        fetch('bbs', 'https://api.bigballsdata.com', '/v1/matches', {'date': '2026-10-07'}, key='k', opener=opener)
    assert exc.value.receipt['status'] == 429
    assert sleeps == [15, 15]
