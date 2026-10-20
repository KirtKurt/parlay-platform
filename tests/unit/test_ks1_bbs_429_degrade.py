import json
from unittest.mock import MagicMock
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture

def test_bbs_unavailable():
    assert bbs_unavailable({'provider': 'bbs', 'status': 429})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 500})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})

def test_degraded_bbs_capture():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    result = degraded_bbs_capture(receipt)
    assert result['payload'] == {'data': []}
    assert result['receipt']['degraded'] == 'bbs_unavailable'
    assert 'sha256' in result['receipt']
