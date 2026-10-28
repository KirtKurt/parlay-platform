"""BBS 429 must degrade to empty catalogue and not fail the slate."""
import json
from pathlib import Path
from unittest.mock import patch

from ks1.live_inputs import bbs_rate_limit_degraded, degraded_bbs_capture, ProviderFailure
from ks1.inventory import encode


def test_bbs_429_is_degraded_not_error():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_rate_limit_degraded(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded['payload'] == {'data': []}
    assert degraded['receipt']['degraded'] == 'bbs_rate_limit'
    assert 'bbs_status' in degraded['receipt']


def test_non_429_bbs_still_error():
    receipt = {'provider': 'bbs', 'status': 500}
    assert not bbs_rate_limit_degraded(receipt)
