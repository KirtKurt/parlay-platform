from __future__ import annotations

import unittest

from soccer_auto.config import (
    ALL_BOOKMAKER_REGIONS,
    HISTORICAL_FEATURED_START,
    HISTORICAL_FEATURED_START_BY_SPORT,
    PUBLISHED_KEYS,
    SOCCER_MARKET_SEEDS,
)


class ProviderCoverageContractTests(unittest.TestCase):
    def test_published_fallback_has_all_current_soccer_keys(self) -> None:
        self.assertEqual(len(PUBLISHED_KEYS), 67)
        self.assertEqual(len(set(PUBLISHED_KEYS)), 67)
        self.assertTrue(all(key.startswith("soccer_") for key in PUBLISHED_KEYS))

    def test_every_published_soccer_key_has_an_official_historical_start(self) -> None:
        self.assertEqual(
            set(HISTORICAL_FEATURED_START_BY_SPORT),
            set(PUBLISHED_KEYS),
        )
        self.assertEqual(HISTORICAL_FEATURED_START, "2020-06-06T10:05:00Z")
        self.assertEqual(
            HISTORICAL_FEATURED_START_BY_SPORT["soccer_epl"],
            "2020-06-06T10:05:00Z",
        )
        self.assertEqual(
            HISTORICAL_FEATURED_START_BY_SPORT[
                "soccer_france_coupe_de_france"
            ],
            "2026-02-26T13:35:37Z",
        )

    def test_all_current_bookmaker_region_groups_are_probed(self) -> None:
        self.assertEqual(
            set(ALL_BOOKMAKER_REGIONS),
            {"us", "us2", "us_dfs", "us_ex", "uk", "eu", "fr", "se", "au"},
        )

    def test_market_seed_covers_featured_derivative_and_player_families(self) -> None:
        required = {
            "h2h", "spreads", "totals", "btts", "draw_no_bet", "double_chance",
            "correct_score", "corners_1x2", "halftime_fulltime", "to_qualify",
            "player_goal_scorer_anytime", "player_shots", "player_assists",
        }
        self.assertTrue(required.issubset(SOCCER_MARKET_SEEDS))


if __name__ == "__main__":
    unittest.main()
