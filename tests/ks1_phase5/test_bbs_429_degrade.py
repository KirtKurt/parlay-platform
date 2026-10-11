import json
from pathlib import Path
from unittest.mock import patch
import pytest
from ks1.live_inputs import capture, ProviderFailure


def test_bbs_429_degrades_to_empty_catalogue(tmp_path):
    def fake_fetch(provider, *args, **kwargs):
        if provider == 'bbs':
            raise ProviderFailure({'provider': 'bbs', 'status': 429, 'body_shape': None})
        if provider == 'official_schedule':
            return {'payload': {'dates': [{'games': []}]}, 'receipt': {'provider': provider, 'status': 200}}
        if provider == 'odds':
            raise ProviderFailure({'provider': 'odds', 'status': 401, 'body_shape': None})
        raise ProviderFailure({'provider': provider, 'status': 500})

    with patch('ks1.live_inputs.fetch', side_effect=fake_fetch), \
         patch('ks1.live_inputs.aws_clients', return_value=(None, None, 'bucket')), \
         patch('ks1.live_inputs.Reader'), \
         patch('os.environ.get', return_value='fakekey'):
        capture('2026-10-11', tmp_path)

    bbs = json.loads((tmp_path / 'bbs.json').read_text())
    assert bbs['payload']['data'] == []
    assert bbs['receipt']['degraded'] == 'bbs_unavailable'
    assert not (tmp_path / 'capture.json').exists() or 'errors' not in json.loads((tmp_path / 'capture.json').read_text()) or not json.loads((tmp_path / 'capture.json').read_text()).get('errors')
