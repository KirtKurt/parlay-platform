import unittest
import pandas as pd
from soccer_predictor.state import build_features
from soccer_predictor.elo import update
from soccer_predictor.blend import ml_weight


class LeakageTests(unittest.TestCase):
    def test_rolling_features_are_pre_match(self):
        rows = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01", tz="UTC"), "div":"E0", "season":"2526", "home":"A", "away":"B", "hg":4, "ag":0, "y":0, "match_id":"1"},
            {"date": pd.Timestamp("2026-01-08", tz="UTC"), "div":"E0", "season":"2526", "home":"A", "away":"C", "hg":1, "ag":1, "y":1, "match_id":"2"},
        ])
        features, _ = build_features(rows)
        first = features.iloc[0]
        second = features.iloc[1]
        self.assertEqual(first.home_n8, 0)
        self.assertAlmostEqual(second.home_gf8, 4.0)
        self.assertAlmostEqual(second.home_ga8, 0.0)
        self.assertAlmostEqual(second.home_pts5, 3.0)

    def test_elo_for_match_is_pre_match(self):
        rows = pd.DataFrame([
            {"date": pd.Timestamp("2026-01-01", tz="UTC"), "div":"E0", "season":"2526", "home":"A", "away":"B", "hg":3, "ag":0, "y":0, "match_id":"1"},
            {"date": pd.Timestamp("2026-01-08", tz="UTC"), "div":"E0", "season":"2526", "home":"A", "away":"B", "hg":0, "ag":1, "y":2, "match_id":"2"},
        ])
        features, _ = build_features(rows)
        self.assertAlmostEqual(features.iloc[0].home_elo, 1500.0)
        expected_home, expected_away = update(1500.0, 1500.0, 3, 0)
        self.assertAlmostEqual(features.iloc[1].home_elo, expected_home)
        self.assertAlmostEqual(features.iloc[1].away_elo, expected_away)

    def test_blend_weight_boundaries(self):
        self.assertEqual(ml_weight(0), .50)
        self.assertEqual(ml_weight(119.999), .50)
        self.assertEqual(ml_weight(120), .40)
        self.assertEqual(ml_weight(-199.999), .40)
        self.assertEqual(ml_weight(200), .28)
        self.assertEqual(ml_weight(-250), .28)


if __name__ == "__main__":
    unittest.main()
