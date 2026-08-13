from __future__ import annotations

import unittest

from soccer_auto.market_features import FEATURE_NAMES, compile_features


def event(extra=True):
    markets = [
        {
            "key": "h2h",
            "outcomes": [
                {"name": "Home", "price": 2.2},
                {"name": "Draw", "price": 3.2},
                {"name": "Away", "price": 3.5},
            ],
        },
        {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}]},
    ]
    if extra:
        markets.extend(
            [
                {
                    "key": "player_shots",
                    "outcomes": [{"name": "Over", "description": "Player", "price": 1.8, "point": 2.5}],
                },
                {"key": "alternate_totals_cards", "outcomes": [{"name": "Over", "price": 2.0, "point": 4.5}]},
                {"key": "new_runtime_market", "outcomes": [{"name": "Yes", "price": 2.4}]},
            ]
        )
    return {
        "sport_key": "soccer_new_runtime_league",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [{"key": "book", "markets": markets}],
    }


class AllMarketFeatureTests(unittest.TestCase):
    def test_every_runtime_market_contributes_before_lock(self) -> None:
        result = compile_features(event(True), earliest=event(False), hours_to_start=0.75)
        self.assertEqual(len(result["values"]), len(FEATURE_NAMES))
        self.assertEqual(result["all_market_count"], 5)
        self.assertEqual(result["all_book_market_pair_count"], 5)
        self.assertEqual(len(result["market_prior"]), 3)
        self.assertAlmostEqual(sum(result["market_prior"]), 1.0)


if __name__ == "__main__":
    unittest.main()
