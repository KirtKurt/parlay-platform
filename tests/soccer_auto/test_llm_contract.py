from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.llm_analyst import (  # noqa: E402
    _context,
    latest_llm_trials,
    validate_analysis,
)


class Ops:
    def __init__(self, rows=None, latest=None, autonomy=None):
        self.rows = rows or []
        self.latest = latest
        self.autonomy = autonomy or {}

    def scan(self, **kwargs):
        return {"Items": self.rows}

    def get_item(self, *, Key, **kwargs):
        if Key == {"PK": "AUTONOMY", "SK": "STATE"}:
            return {"Item": self.autonomy}
        if Key == {"PK": "LLM_ANALYSIS", "SK": "LATEST"} and self.latest:
            return {"Item": self.latest}
        return {}


class Store:
    def __init__(self, ops):
        self.ops = ops

    def list_competitions(self):
        return [{"sport_key": "soccer_epl", "active": True, "has_outrights": False}]

    def model_items(self):
        return []


class LlmBoundaryTests(unittest.TestCase):
    def test_untrusted_trials_are_clamped_and_deduplicated(self) -> None:
        payload = {
            "summary": "soccer only",
            "coverage_findings": ["one"],
            "warnings": ["no leakage"],
            "recommended_trials": [
                {"learning_rate": 0.03, "l2": 0.001, "epochs": 60, "rationale": "valid"},
                {"learning_rate": 0.03, "l2": 0.001, "epochs": 60, "rationale": "duplicate"},
                {"learning_rate": 5, "l2": 0.001, "epochs": 60, "rationale": "unsafe"},
                {"learning_rate": 0.02, "l2": 0.001, "epochs": 5000, "rationale": "unsafe"},
            ],
        }
        result = validate_analysis(payload)
        self.assertEqual(result["validation_status"], "VALIDATED")
        self.assertEqual(len(result["recommended_trials"]), 1)
        self.assertIn("analysis_digest", result)

    def test_llm_output_cannot_add_training_controls(self) -> None:
        result = validate_analysis(
            {
                "recommended_trials": [],
                "promotion_gate": "disable",
                "prediction": {"home": 1.0},
                "target": "away",
            }
        )
        self.assertNotIn("promotion_gate", result)
        self.assertNotIn("prediction", result)
        self.assertNotIn("target", result)

    def test_context_contains_missing_pair_and_failure_diagnostics(self) -> None:
        rows = [
            {
                "entity_type": "SOCCER_MARKET_INVENTORY",
                "inventory": {"book": {"markets": ["h2h", "totals"]}},
            },
            {
                "entity_type": "SOCCER_EVENT_COVERAGE_PLAN",
                "event_key": "event",
                "observed_at": "2026-08-14T04:00:00Z",
                "expected_pairs": ["book|h2h", "book|totals"],
            },
            {
                "entity_type": "SOCCER_EVENT_COVERAGE_FETCH",
                "event_key": "event",
                "plan_observed_at": "2026-08-14T04:00:00Z",
                "returned_pairs": ["book|h2h"],
            },
            {
                "entity_type": "SOCCER_COLLECTION_FAILURE",
                "event_key": "event",
                "operation": "event_odds",
                "permanent": True,
                "observed_at": "2026-08-14T04:01:00Z",
                "detail": "unsupported singleton",
            },
        ]
        context = _context(
            Store(
                Ops(
                    rows,
                    autonomy={
                        "authority": "DEGRADED",
                        "component_liveness_complete": False,
                        "component_liveness": {"freeze": {"healthy": False}},
                    },
                )
            )
        )
        coverage = context["coverage"]
        self.assertEqual(coverage["unique_bookmakers_seen"], 1)
        self.assertEqual(coverage["unique_markets_seen"], 2)
        self.assertEqual(coverage["expected_pairs"], 2)
        self.assertEqual(coverage["fetched_pairs"], 1)
        self.assertEqual(coverage["missing_pairs"], 1)
        self.assertEqual(coverage["permanent_collection_failures"], 1)
        self.assertFalse(context["autonomy"]["component_liveness_complete"])

    def test_expired_analysis_cannot_control_a_future_training_search(self) -> None:
        validated = validate_analysis(
            {
                "recommended_trials": [
                    {"learning_rate": 0.03, "l2": 0.001, "epochs": 60}
                ]
            }
        )
        row = {
            **validated,
            "created_at": "2026-08-10T00:00:00Z",
            "expires_at": int(datetime(2026, 8, 11, tzinfo=timezone.utc).timestamp()),
        }
        with patch(
            "soccer_auto.llm_analyst.now_utc",
            return_value=datetime(2026, 8, 14, tzinfo=timezone.utc),
        ):
            trials, analysis_digest = latest_llm_trials(Store(Ops(latest=row)))
        self.assertEqual(trials, [])
        self.assertIsNone(analysis_digest)


if __name__ == "__main__":
    unittest.main()
