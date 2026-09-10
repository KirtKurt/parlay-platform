import unittest
from soccer_predictor.predict import btts_label


class BTTSTests(unittest.TestCase):
    def test_btts_definition(self):
        self.assertFalse(btts_label(0, 0))
        self.assertTrue(btts_label(1, 1))
        self.assertFalse(btts_label(2, 0))


if __name__ == "__main__":
    unittest.main()
