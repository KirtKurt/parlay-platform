"""BBS 429/network/missing-key must degrade to empty catalogue, not kill the slate."""
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_bbs_unavailable_matches_rate_limit_and_transport():
    assert bbs_unavailable({'provider': 'bbs', 'status': 429})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'BBS_API_KEY_MISSING'})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 200})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 500})


def test_degraded_bbs_capture_empty_catalogue():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    result = degraded_bbs_capture(receipt)
    assert result['payload'] == {'data': []}
    assert result['receipt']['degraded'] == 'bbs_unavailable'
    assert result['receipt']['match_catalogue'] == 'empty'
    assert 'sha256' in result['receipt']


def test_degraded_bbs_capture_rejects_non_unavailable():
    try:
        degraded_bbs_capture({'provider': 'bbs', 'status': 200})
        assert False, 'should have raised'
    except ValueError as exc:
        assert 'non-unavailable' in str(exc)
