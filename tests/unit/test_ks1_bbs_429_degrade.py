"""BBS 429/network must not kill the slate."""
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure

def test_bbs_429_is_unavailable():
    receipt = {'provider': 'bbs', 'status': 429}
    assert bbs_unavailable(receipt)

def test_degraded_empty_catalogue():
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {}}
    out = degraded_bbs_capture(receipt)
    assert out['payload'] == {'data': []}
    assert out['receipt']['degraded'] == 'bbs_unavailable'

def test_non_429_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200}
    assert not bbs_unavailable(receipt)
    try:
        degraded_bbs_capture(receipt)
        assert False
    except ValueError:
        pass
