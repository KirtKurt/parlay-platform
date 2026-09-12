from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.kss1_engine import ENGINE_ID  # noqa: E402
from soccer_auto.kss1_runtime import (  # noqa: E402
    build_kss1_shadow_item,
    grade_kss1_against_settlement,
    kss1_prediction_sk,
    market_prior_from_lock,
)


def sample_lock():
    return {
        "event_key": "EVENT#soccer_epl#abc",
        "event_id": "abc",
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-09-12T16:00:00Z",
        "schedule_revision": 4,
        "lock_at": "2026-09-12T15:00:00Z",
        "feature_hash": "feat",
        "prediction_eligible": True,
        "frozen_features": {"market_prior": (0.48, 0.27, 0.25)},
    }


class RuntimeTests(unittest.TestCase):
    def test_prior_and_sk(self):
        lock = sample_lock()
        self.assertEqual(market_prior_from_lock(lock)["home"], 0.48)
        self.assertIn(ENGINE_ID, kss1_prediction_sk(lock))
        self.assertIn("#REV#4#", kss1_prediction_sk(lock))

    def test_shadow_item_is_not_public(self):
        item = build_kss1_shadow_item(sample_lock(), "2026-09-12T15:00:00Z")
        self.assertEqual(item["prediction_status"], "SHADOW")
        self.assertFalse(item["automatic_prediction_allowed"])
        self.assertEqual(item["target"], "kss1_book")
        self.assertEqual(item["kss1"]["authority"], "SHADOW_LEARNING")
        self.assertIn("markets", item["kss1"])

    def test_grade_hits_and_abstains(self):
        item = build_kss1_shadow_item(sample_lock(), "2026-09-12T15:00:00Z")
        graded = grade_kss1_against_settlement(
            item, {"home_score": 2, "away_score": 1}
        )
        self.assertEqual(graded["actual"]["1x2"], "home")
        self.assertIn(graded["graded"]["1x2"], {"hit", "miss", "abstain"})


if __name__ == "__main__":
    unittest.main()
