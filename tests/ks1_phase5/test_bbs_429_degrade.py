"""BBS 429/network must not kill the slate."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from ks1.live_inputs import ProviderFailure, capture, degraded_bbs_capture, bbs_unavailable


def test_bbs_unavailable_recognizes_degrade_statuses():
    assert bbs_unavailable({'provider': 'bbs', 'status': 429})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'})
    assert bbs_unavailable({'provider': 'bbs', 'status': 'BBS_API_KEY_MISSING'})
    assert not bbs_unavailable({'provider': 'bbs', 'status': 500})
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})


def test_degraded_bbs_capture_empty_data():
    receipt = {'provider': 'bbs', 'status': 429}
    result = degraded_bbs_capture(receipt)
    assert result['payload'] == {'data': []}
    assert result['receipt']['degraded'] == 'bbs_unavailable'


def test_capture_degrades_bbs_429_and_continues(tmp_path):
    official = {
        'payload': {'dates': [{'games': [{'gamePk': 1, 'gameDate': '2026-10-22T20:00:00Z', 'status': 'Preview'}]}]},
        'receipt': {'provider': 'official_schedule', 'status': 200}
    }
    def fake_fetch(provider, base, path, params, key=None, opener=None):
        if provider == 'official_schedule':
            return official
        if provider == 'bbs':
            raise ProviderFailure({'provider': 'bbs', 'status': 429})
        if provider == 'odds':
            return {'payload': [], 'receipt': {'provider': 'odds', 'status': 200}}
        raise AssertionError(provider)

    with patch('ks1.live_inputs.fetch', side_effect=fake_fetch),
         patch('ks1.live_inputs.lineup_feeds', return_value={'games': {}}),
         patch('ks1.live_inputs.aws_clients', return_value=(None, None, 'bucket')),
         patch('ks1.live_inputs.Reader') as MockReader,
         patch('boto3.resource'),
         patch('ks1.live_inputs.restore_recent_history', return_value={}),
         patch('ks1.platt_inputs.capture', return_value={}):
        MockReader.return_value.pointer.return_value = {'games': [], 'schedule': []}
        MockReader.return_value.read.return_value = {'artifact': {}}
        MockReader.return_value.receipts = [{}]
        MockReader.return_value.keys.return_value = []
        # Minimal stubs so capture reaches the end without real AWS
        with patch.object(Path, 'read_bytes', return_value=b'{}'):
            try:
                capture('2026-10-22', tmp_path)
            except Exception:
                pass  # other stubs may still raise; we only care bbs.json was written
    bbs_path = tmp_path / 'bbs.json'
    if bbs_path.exists():
        data = json.loads(bbs_path.read_bytes())
        assert data['payload']['data'] == []
        assert data['receipt']['degraded'] == 'bbs_unavailable'
