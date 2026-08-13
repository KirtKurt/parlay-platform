from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.settlement import (  # noqa: E402
    build_settlement,
    regulation_time_ambiguous,
    settlement_handler,
)


class SettlementStore:
    def __init__(self):
        self.claims = []

    def active_events_between(self, start, end):
        return [{"sport_key": "soccer_future_league"}]

    def list_competitions(self):
        return [{"sport_key": "soccer_future_league", "scores_supported": True}]

    def claim_job(self, claim, expires_at):
        self.claims.append(claim)
        return False


class NeverScoresClient:
    def scores(self, *args, **kwargs):
        raise AssertionError("duplicate scheduled cycle must not call scores")


class SettlementCadenceTests(unittest.TestCase):
    def test_duplicate_scheduler_delivery_is_claimed_before_scores_call(self) -> None:
        store = SettlementStore()
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.settlement.SoccerStore", return_value=store), patch(
            "soccer_auto.settlement._client", return_value=NeverScoresClient()
        ), patch("soccer_auto.settlement.now_utc", return_value=now):
            result = settlement_handler({}, None)
        self.assertEqual(result["competitions_checked"], 1)
        self.assertEqual(result["failures"], [])
        self.assertEqual(len(store.claims), 1)

    def test_dynamic_cup_key_is_never_a_regulation_time_label(self) -> None:
        self.assertTrue(
            regulation_time_ambiguous(
                "soccer_future_federation_cup",
                competition={"title": "Future Federation Cup"},
            )
        )

    def test_to_qualify_market_quarantines_a_league_playoff_fixture(self) -> None:
        self.assertTrue(
            regulation_time_ambiguous(
                "soccer_future_league",
                competition={"title": "Future League"},
                event_markets={"h2h", "to_qualify"},
            )
        )

    def test_dynamic_quarantine_reaches_settlement_training_gate(self) -> None:
        row = {
            "id": "event-1",
            "sport_key": "soccer_future_league",
            "schedule_revision": 1,
            "commence_time": "2026-08-14T10:00:00Z",
            "completed": True,
            "home_team": "Home",
            "away_team": "Away",
            "scores": [
                {"name": "Home", "score": "2"},
                {"name": "Away", "score": "1"},
            ],
        }
        settlement = build_settlement(
            row,
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )
        self.assertFalse(settlement["training_eligible_1x2"])
        self.assertTrue(settlement["regulation_time_ambiguous"])


if __name__ == "__main__":
    unittest.main()
