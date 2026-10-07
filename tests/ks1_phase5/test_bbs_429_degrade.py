"""BBS 429 is a missing catalogue, not a slate-killing identity error."""
from io import BytesIO
from urllib.error import HTTPError

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure, fetch


def test_429_and_transport_are_unavailable_not_identity():
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {'error': 'dict'}}
    assert bbs_unavailable(receipt)
    assert bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})


def test_degraded_catalogue_is_empty_and_marked():
    out = degraded_bbs_capture({'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'})
    assert out['payload'] == {'data': []}
    assert out['receipt']['degraded'] == 'bbs_unavailable'
    assert out['receipt']['match_catalogue'] == 'empty'
    try:
        degraded_bbs_capture({'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'})
    except ValueError:
        pass
    else:
        raise AssertionError('truncation must stay fatal')


def test_fetch_retries_429_then_raises(monkeypatch):
    sleeps = []
    monkeypatch.setattr('ks1.live_inputs.time.sleep', lambda s: sleeps.append(s))
    calls = {'n': 0}

    def opener(request, timeout=25):
        calls['n'] += 1
        raise HTTPError(request.full_url, 429, 'rate', hdrs={}, fp=BytesIO(b'{"error":{"code":"rate_limit"}}'))

    try:
        fetch('bbs', 'https://api.bigballsdata.com', '/v1/matches', {'sport': 'baseball'}, key='k', opener=opener)
    except ProviderFailure as exc:
        assert exc.receipt['status'] == 429
    else:
        raise AssertionError('exhausted 429 must still surface')
    assert calls['n'] == 3
    assert sleeps == [15, 15]
