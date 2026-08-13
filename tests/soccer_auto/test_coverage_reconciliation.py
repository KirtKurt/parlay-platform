from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.api import _latest_cycle_coverage  # noqa: E402


class CoverageCycleTests(unittest.TestCase):
    def test_old_fetch_cannot_satisfy_new_plan(self) -> None:
        plans = [
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:00:00Z",
                "expected_pairs": ["book|h2h"],
            },
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:15:00Z",
                "expected_pairs": ["book|h2h", "book|player_shots"],
            },
        ]
        fetches = [
            {
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:00:00Z",
                "returned_pairs": ["book|h2h", "book|player_shots"],
            }
        ]
        result = _latest_cycle_coverage(plans, fetches)
        self.assertEqual(len(result["expected_pairs"]), 2)
        self.assertEqual(result["returned_pairs"], set())
        self.assertEqual(len(result["missing_pairs"]), 2)
        self.assertFalse(result["cycles"][0]["complete"])

    def test_latest_matching_cycle_reconciles_exact_pairs(self) -> None:
        plans = [
            {
                "event_key": "event",
                "observed_at": "2026-08-14T04:15:00Z",
                "expected_pairs": ["book|h2h", "book|totals"],
            }
        ]
        fetches = [
            {
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:15:00Z",
                "returned_pairs": ["book|h2h", "book|totals", "book|new_market"],
            }
        ]
        result = _latest_cycle_coverage(plans, fetches)
        self.assertFalse(result["missing_pairs"])
        self.assertEqual(len(result["returned_pairs"]), 2)
        self.assertTrue(result["cycles"][0]["complete"])


if __name__ == "__main__":
    unittest.main()
