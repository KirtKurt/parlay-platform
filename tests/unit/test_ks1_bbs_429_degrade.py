from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture, ProviderFailure

def test_bbs_unavailable_and_degrade():
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {}}
    assert bbs_unavailable(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded['payload']['data'] == []
    assert degraded['receipt']['degraded'] == 'bbs_unavailable'
    assert not bbs_unavailable({'provider': 'bbs', 'status': 200})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})
