from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.settlement import (  # noqa: E402
    _record_conflict,
    build_settlement,
    regulation_time_ambiguous,
    settlement_conflict_blocks_training,
    settlement_handler,
    settlement_records_equivalent,
)
from soccer_auto.canonical import digest  # noqa: E402


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


def completed_score() -> dict:
    return {
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
        settlement = build_settlement(
            completed_score(),
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )
        self.assertFalse(settlement["training_eligible_1x2"])
        self.assertTrue(settlement["regulation_time_ambiguous"])

    def test_legacy_digest_schema_is_idempotent_but_score_change_is_not(self) -> None:
        candidate = build_settlement(
            completed_score(), observed_at="2026-08-14T12:00:00Z"
        )
        legacy = {key: value for key, value in candidate.items() if key != "schedule_identity"}
        legacy["settlement_digest"] = digest(
            {
                key: candidate[key]
                for key in (
                    "event_key",
                    "schedule_revision",
                    "commence_time",
                    "sport_key",
                    "home_team",
                    "away_team",
                    "home_score",
                    "away_score",
                    "result_1x2",
                    "settlement_semantics",
                )
            }
        )
        audit_row = {
            "training_blocked": True,
            "existing": legacy,
            "candidate": candidate,
        }
        self.assertTrue(settlement_records_equivalent(legacy, candidate))
        self.assertFalse(settlement_conflict_blocks_training(audit_row))

        changed = {**candidate, "home_score": 3, "correct_score": "3-1"}
        changed["settlement_digest"] = digest(
            {
                "event_key": changed["event_key"],
                "schedule_revision": changed["schedule_revision"],
                "schedule_identity": changed["schedule_identity"],
                "commence_time": changed["commence_time"],
                "sport_key": changed["sport_key"],
                "home_team": changed["home_team"],
                "away_team": changed["away_team"],
                "home_score": changed["home_score"],
                "away_score": changed["away_score"],
                "result_1x2": changed["result_1x2"],
                "settlement_semantics": changed["settlement_semantics"],
            }
        )
        self.assertFalse(settlement_records_equivalent(legacy, changed))
        self.assertTrue(
            settlement_conflict_blocks_training(
                {
                    "training_blocked": True,
                    "existing": legacy,
                    "candidate": changed,
                }
            )
        )

    def test_schedule_identity_conflict_always_blocks_training(self) -> None:
        self.assertTrue(
            settlement_conflict_blocks_training(
                {
                    "training_blocked": True,
                    "reason": "SCORE_SCHEDULE_IDENTITY_MISMATCH",
                }
            )
        )

    def test_repeated_identical_conflict_uses_one_deterministic_key(self) -> None:
        candidate = build_settlement(
            completed_score(), observed_at="2026-08-14T12:00:00Z"
        )
        existing = {**candidate, "home_score": 3, "correct_score": "3-1"}
        existing["settlement_digest"] = "different-signed-evidence"

        class Ops:
            def __init__(self):
                self.items = []

            def put_item(self, **kwargs):
                self.items.append(kwargs["Item"])

        class Store:
            ops = Ops()

        store = Store()
        _record_conflict(store, existing, candidate, "2026-08-14T12:01:00Z")
        _record_conflict(store, existing, candidate, "2026-08-14T12:06:00Z")
        self.assertEqual(store.ops.items[0]["SK"], store.ops.items[1]["SK"])
        self.assertEqual(
            store.ops.items[0]["conflict_evidence_digest"],
            store.ops.items[1]["conflict_evidence_digest"],
        )

    def test_untracked_completed_score_is_not_a_settlement_conflict(self) -> None:
        class Response:
            data = [completed_score()]

        class Client:
            def scores(self, *args, **kwargs):
                return Response()

        class Ops:
            def __init__(self):
                self.items = []

            def put_item(self, **kwargs):
                self.items.append(kwargs["Item"])

        class Store(SettlementStore):
            def __init__(self):
                super().__init__()
                self.ops = Ops()

            def claim_job(self, claim, expires_at):
                return True

            def provider_budget_available(self, *args, **kwargs):
                return True

            def record_quota(self, *args, **kwargs):
                return None

            def archive_json(self, *args, **kwargs):
                return None

            def get_event(self, event_key):
                return None

        store = Store()
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.settlement.SoccerStore", return_value=store), patch(
            "soccer_auto.settlement._client", return_value=Client()
        ), patch("soccer_auto.settlement.now_utc", return_value=now):
            result = settlement_handler({}, None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["untracked_completed_scores"], 1)
        self.assertEqual(result["conflicts"], 0)
        self.assertEqual(store.ops.items, [])


if __name__ == "__main__":
    unittest.main()
