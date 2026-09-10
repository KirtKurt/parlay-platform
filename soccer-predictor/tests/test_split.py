import unittest
import numpy as np
import pandas as pd
from soccer_predictor.model import Model
from soccer_predictor.io import day


class SplitTests(unittest.TestCase):
    def test_training_window_is_date_based_and_strictly_pre_cutoff(self):
        dates = pd.date_range("2021-01-01", periods=180, freq="7D", tz="UTC")
        rows = []
        outcomes = [0, 1, 2]
        for i, d in enumerate(dates):
            rows.append({
                "date": d, "div": "E0", "season": str(d.year),
                "home": f"H{i%10}", "away": f"A{i%10}",
                "hg": 2 if outcomes[i%3] == 0 else 1 if outcomes[i%3] == 1 else 0,
                "ag": 0 if outcomes[i%3] == 0 else 1 if outcomes[i%3] == 1 else 2,
                "y": outcomes[i%3], "match_id": str(i),
                "feature_asof": (d - pd.Timedelta(days=1)).isoformat(),
                "elo_gap": float((i % 9) * 10 - 40),
                "home_gf8": 1.2 + (i % 5)*.1, "home_ga8": 1.0 + (i % 4)*.1,
                "away_gf8": 1.1 + (i % 6)*.1, "away_ga8": 1.2 + (i % 3)*.1,
                "home_pts5": float(i % 4), "away_pts5": float((i+1) % 4),
                "market_feature_safe": False,
            })
        frame = pd.DataFrame(rows)
        cutoff = day("2024-06-01")
        model = Model().fit(frame, cutoff, min_rows=100)
        self.assertEqual(day(model.meta["fit_cutoff"]), cutoff)
        self.assertLess(day(model.meta["train_max_date"]), cutoff)
        self.assertGreaterEqual(day(model.meta["train_min_date"]), cutoff - pd.DateOffset(years=4))


if __name__ == "__main__":
    unittest.main()
