"""BBS 429 must not kill the official slate."""
import json
from pathlib import Path
from unittest.mock import patch

from ks1.live_inputs import bbs_rate_limited, degraded_bbs_capture, ProviderFailure


def test_bbs_rate_limited_degrades_to_empty_catalogue(tmp_path):
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {'error': {'code': 'str'}}}
    assert bbs_rate_limited(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded['payload'] == {'data': []}
    assert degraded['receipt']['degraded'] == 'bbs_rate_limited'
    assert 'sha256' in degraded['receipt']


def test_non_429_bbs_failure_still_errors():
    receipt = {'provider': 'bbs', 'status': 500, 'body_shape': None}
    assert not bbs_rate_limited(receipt)
