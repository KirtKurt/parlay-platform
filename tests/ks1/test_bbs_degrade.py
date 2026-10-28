import unittest
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture

class TestBBSDegrade(unittest.TestCase):
    def test_unavailable(self):
        self.assertTrue(bbs_unavailable({'provider': 'bbs', 'status': 429}))
        self.assertTrue(bbs_unavailable({'provider': 'bbs', 'status': 'NETWORK_ERROR'}))
        self.assertFalse(bbs_unavailable({'provider': 'bbs', 'status': 200}))
        self.assertFalse(bbs_unavailable({'provider': 'odds', 'status': 429}))

    def test_degrade(self):
        receipt = {'provider': 'bbs', 'status': 429, 'body_shape': {}}
        result = degraded_bbs_capture(receipt)
        self.assertEqual(result['payload'], {'data': []})
        self.assertEqual(result['receipt']['degraded'], 'bbs_unavailable')
        self.assertIn('sha256', result['receipt'])

    def test_refuse_non_unavailable(self):
        with self.assertRaises(ValueError):
            degraded_bbs_capture({'provider': 'bbs', 'status': 500})

if __name__ == '__main__':
    unittest.main()
