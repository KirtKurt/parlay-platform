"""BBS 429 / network / missing key degrades to empty catalogue; does not kill slate."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture, capture


def test_bbs_unavailable_statuses():
    assert bbs_unavailable({'provider': 'bbs', 'status': 429})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'BBS_API_KEY_MISSING'})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 500})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})


def test_degraded_bbs_empty_data():
    receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {}}
    result = degraded_bbs_capture(receipt)
    assert result['payload'] == {'data': []}
    assert result['receipt']['degraded'] == 'bbs_unavailable'
    assert 'sha256' in result['receipt']


def test_capture_continues_on_bbs_429(tmp_path):
    def fake_fetch(provider, base, path, params, *, key=None, opener=None):
        if provider == 'bbs':
            from ks1.live_inputs import ProviderFailure
            raise ProviderFailure({'provider': 'bbs', 'status': 429, 'body_shape': {}})
        if provider == 'official_schedule':
            return {'payload': {'dates': [{'games': []}]}, 'receipt': {'status': 200}}
        if provider == 'odds':
            return {'payload': [], 'receipt': {'status': 200}}
        raise ValueError(provider)

    with patch('ks1.live_inputs.fetch', side_effect=fake_fetch), \
         patch('ks1.live_inputs.aws_clients', return_value=(None, None, 'bucket')), \
         patch('ks1.live_inputs.lineup_feeds', return_value={'games': {}}), \
         patch('ks1.inventory.Reader') as MockReader, \
         patch('ks1.platt_inputs.capture', return_value={}), \
         patch('ks1.recent_statcast.restore_recent_history', return_value={}), \
         patch('boto3.resource'):
        MockReader.return_value.pointer.return_value = {'games': []}
        MockReader.return_value.read.return_value = {'artifact': {}}
        MockReader.return_value.receipts = []
        MockReader.return_value.keys.return_value = []
        try:
            capture('2026-10-12', tmp_path)
        except Exception:
            pass
        bbs_path = tmp_path / 'bbs.json'
        assert bbs_path.exists()
        bbs = json.loads(bbs_path.read_text())
        assert bbs['payload']['data'] == []
        assert bbs['receipt'].get('degraded') == 'bbs_unavailable'
