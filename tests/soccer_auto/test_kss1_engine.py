from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.kss1_engine import grade_lock, predict_match  # noqa: E402
from soccer_auto.kss1_identity import classify_competition, map_event, normalize_name  # noqa: E402
from soccer_auto.kss1_lock import classify_observation, void_for_postponement  # noqa: E402
from soccer_auto.kss1_markets import markets_from_grid, score_matrix, settle_regulation  # noqa: E402


class MarketsTests(unittest.TestCase):
    def test_grid_sums_and_derives_four_markets(self):
        grid = score_matrix(1.6, 1.1)
        markets = markets_from_grid(grid)
        self.assertAlmostEqual(markets["p_home"] + markets["p_draw"] + markets["p_away"], 1.0, places=6)
        self.assertAlmostEqual(markets["p_over_25"] + markets["p_under_25"], 1.0, places=6)
        self.assertAlmostEqual(markets["p_btts_yes"] + markets["p_btts_no"], 1.0, places=6)
        self.assertAlmostEqual(markets["p_1x"], markets["p_home"] + markets["p_draw"], places=9)

    def test_settlement_labels(self):
        self.assertEqual(settle_regulation(2, 1)["1x2"], "home")
        self.assertEqual(settle_regulation(1, 1)["btts"], "yes")
        self.assertEqual(settle_regulation(2, 0)["over_25"], "under")
        self.assertEqual(settle_regulation(2, 1)["over_25"], "over")


class IdentityTests(unittest.TestCase):
    def test_aliases_and_tiering(self):
        self.assertEqual(normalize_name("Man Utd"), "manchester united")
        self.assertEqual(classify_competition("soccer_epl")["tier"], "A")
        self.assertEqual(classify_competition("soccer_fa_cup")["tier"], "Q")
        self.assertFalse(classify_competition("soccer_fa_cup")["goals_model_eligible"])

    def test_refuses_ambiguous_join(self):
        rows = [
            {"id": "11111111-1111-1111-1111-111111111111", "home": "Arsenal", "away": "Chelsea", "kickoff_utc": "2026-09-12T15:00:00Z"},
            {"id": "22222222-2222-2222-2222-222222222222", "home": "Arsenal", "away": "Chelsea", "kickoff_utc": "2026-09-12T15:00:00Z"},
        ]
        mapped = map_event(
            odds_event_id="evt-1",
            sport_key="soccer_epl",
            home_team="Arsenal",
            away_team="Chelsea",
            commence_time="2026-09-12T15:00:00Z",
            bbd_matches=rows,
        )
        self.assertEqual(mapped["status"], "review")
        self.assertIsNone(mapped["bbd_match_id"])

    def test_unique_uuid_join(self):
        rows = [
            {"id": "11111111-1111-1111-1111-111111111111", "home": "Arsenal", "away": "Chelsea", "kickoff_utc": "2026-09-12T15:00:00Z"},
        ]
        mapped = map_event(
            odds_event_id="evt-1",
            sport_key="soccer_epl",
            home_team="Arsenal",
            away_team="Chelsea",
            commence_time="2026-09-12T15:00:00Z",
            bbd_matches=rows,
        )
        self.assertEqual(mapped["status"], "mapped")
        self.assertEqual(mapped["bbd_match_id"], "11111111-1111-1111-1111-111111111111")


class LockTests(unittest.TestCase):
    def test_t60_and_missed_window(self):
        kickoff = "2026-09-12T16:00:00Z"
        on_time = classify_observation(kickoff, "2026-09-12T15:00:00Z")
        late = classify_observation(kickoff, "2026-09-12T15:20:00Z")
        self.assertEqual(on_time["action"], "public_eligible")
        self.assertEqual(late["action"], "no_public_pick")

    def test_postponement_voids(self):
        result = void_for_postponement(
            {"commence_time": "2026-09-12T16:00:00Z", "lock_version": "kss1-t60-lock-v1"},
            "2026-09-12T18:00:00Z",
        )
        self.assertTrue(result["void"])
        self.assertFalse(result["relock_on_lineup"])


class EngineTests(unittest.TestCase):
    def test_shadow_predict_and_grade(self):
        prediction = predict_match(
            {
                "odds_event_id": "evt-1",
                "sport_key": "soccer_epl",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2026-09-12T16:00:00Z",
                "observed_at": "2026-09-12T15:00:00Z",
                "home_attack": 1.2,
                "away_attack": 1.05,
                "home_defence": 0.9,
                "away_defence": 1.0,
                "xg_home": 1.7,
                "xg_away": 1.1,
                "market_1x2": {"home": 0.48, "draw": 0.27, "away": 0.25},
                "bbd_matches": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "home": "Arsenal",
                        "away": "Chelsea",
                        "kickoff_utc": "2026-09-12T16:00:00Z",
                    }
                ],
            }
        )
        self.assertEqual(prediction["authority"], "SHADOW_LEARNING")
        self.assertFalse(prediction["automatic_prediction_allowed"])
        self.assertTrue(prediction["has_xg"])
        self.assertEqual(prediction["mapping"]["status"], "mapped")
        for key in ("1x2_published", "double_chance_published", "ou25_published", "btts_published"):
            self.assertNotEqual(prediction["markets"][key], "ABSTAIN")
        graded = grade_lock(prediction, 2, 1)
        self.assertEqual(graded["actual"]["1x2"], "home")
        self.assertIn(graded["graded"]["1x2"], {"hit", "miss"})

    def test_odds_only_shadow_book_still_picks(self):
        prediction = predict_match(
            {
                "odds_event_id": "evt-3",
                "sport_key": "soccer_epl",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2026-09-12T16:00:00Z",
                "observed_at": "2026-09-12T15:00:00Z",
                "market_1x2": {"home": 0.52, "draw": 0.25, "away": 0.23},
            }
        )
        self.assertNotEqual(prediction["mapping"]["status"], "mapped")
        self.assertNotEqual(prediction["markets"]["1x2_published"], "ABSTAIN")
        self.assertNotEqual(prediction["markets"]["double_chance_published"], "ABSTAIN")

    def test_quarantine_cup_does_not_publish(self):
        prediction = predict_match(
            {
                "odds_event_id": "evt-2",
                "sport_key": "soccer_fa_cup",
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2026-09-12T16:00:00Z",
                "observed_at": "2026-09-12T15:00:00Z",
            }
        )
        self.assertEqual(prediction["markets"]["1x2_published"], "ABSTAIN")
        self.assertEqual(prediction["markets"]["btts_published"], "ABSTAIN")


if __name__ == "__main__":
    unittest.main()
