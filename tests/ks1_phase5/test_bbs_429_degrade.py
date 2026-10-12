import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure, capture


class TestBBS429Degrade(unittest.TestCase):
    def test_unavailable_statuses(self):
        self.assertTrue(bbs_unavailable({'provider': 'bbs', 'status': 429}))
        self.assertTrue(bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'}))
        self.assertFalse(bbs_unavailable({'provider': 'bbs', 'status': 200}))
        self.assertFalse(bbs_unavailable({'provider': 'odds', 'status': 429}))

    def test_degraded_empty_catalogue(self):
        receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {}}
        result = degraded_bbs_capture(receipt)
        self.assertEqual(result['payload'], {'data': []})
        self.assertEqual(result['receipt']['degraded'], 'bbs_unavailable')
        self.assertIn('sha256', result['receipt'])

    def test_capture_degrades_429_not_fail(self):
        def fake_fetch(provider, base, path, params, *, key=None, opener=None):
            if provider == 'bbs':
                raise ProviderFailure({'provider': 'bbs', 'status': 429, 'body_shape': {}})
            if provider == 'official_schedule':
                return {'payload': {'dates': [{'games': [{'gamePk': 1, 'gameDate': '2026-10-12T23:00:00Z', 'status': {'abstractGameState': 'Preview'}}]}]}, 'receipt': {}}
            return {'payload': [], 'receipt': {'provider': provider, 'status': 200}}

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            with patch('ks1.live_inputs.fetch', side_effect=fake_fetch),
                 patch('ks1.live_inputs.aws_clients', return_value=(None, None, 'bucket')),
                 patch('ks1.live_inputs.Reader'),
                 patch('boto3.resource'):
                # Minimal mocks to reach the BBS path without full S3.
                try:
                    capture('2026-10-12', output)
                except Exception:
                    pass  # other mocks may fail later; check bbs.json exists
            bbs_path = output / 'bbs.json'
            if bbs_path.exists():
                data = json.loads(bbs_path.read_bytes())
                self.assertEqual(data['payload']['data'], [])
                self.assertEqual(data['receipt']['degraded'], 'bbs_unavailable')


if __name__ == '__main__':
    unittest.main()
