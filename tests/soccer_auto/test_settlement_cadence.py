from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.settlement import (  # noqa: E402
    _record_conflict,
    build_settlement,
    build_settlement_admissibility_certificate,
    reconcile_settlement_admissibility,
    regulation_time_ambiguous,
    settlement_admissibility_certificate_valid,
    settlement_conflict_blocks_training,
    settlement_handler,
    settlement_records_equivalent,
    settlement_score_records_equivalent,
    settlement_training_admissible,
    settlement_training_views,
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

    def test_reconciliation_only_action_never_constructs_provider_client(self) -> None:
        store = SettlementStore()
        now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.settlement.SoccerStore", return_value=store), patch(
            "soccer_auto.settlement._client",
            side_effect=AssertionError("reconciliation must not call provider"),
        ), patch("soccer_auto.settlement.now_utc", return_value=now):
            result = settlement_handler(
                {"action": "reconcile_admissibility_only"}, None
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["component"], "settlement")
        self.assertEqual(
            result["admissibility_reconciliation"]["scan_mode"],
            "UNAVAILABLE",
        )

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

    def test_mixed_format_group_fixture_is_classified_at_event_scope(self) -> None:
        self.assertFalse(
            regulation_time_ambiguous(
                "soccer_uefa_champs_league",
                competition={"title": "UEFA Champions League"},
                event_markets={"h2h", "totals"},
            )
        )
        self.assertTrue(
            regulation_time_ambiguous(
                "soccer_uefa_champs_league",
                competition={"title": "UEFA Champions League"},
                event_markets={"h2h", "to_qualify"},
            )
        )

    def test_append_only_certificate_unlocks_old_mixed_format_score(self) -> None:
        score = {
            **completed_score(),
            "sport_key": "soccer_uefa_champs_league",
        }
        settlement = build_settlement(
            score,
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )
        self.assertFalse(settlement_training_admissible(settlement))
        certificate = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={
                "sport_key": "soccer_uefa_champs_league",
                "title": "UEFA Champions League",
            },
            event_markets={"h2h", "totals"},
        )
        self.assertTrue(
            settlement_admissibility_certificate_valid(settlement, certificate)
        )
        view = settlement_training_views([settlement, certificate])[0]
        self.assertTrue(settlement_training_admissible(view))
        self.assertEqual(
            view["training_admissibility_source"],
            "IMMUTABLE_EVENT_SCOPE_CERTIFICATE",
        )
        self.assertTrue(view["regulation_time_ambiguous"])
        self.assertFalse(settlement["training_eligible_1x2"])

    def test_later_to_qualify_certificate_revokes_direct_eligibility(self) -> None:
        settlement = build_settlement(
            completed_score(),
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=False,
        )
        self.assertTrue(settlement_training_admissible(settlement))
        certificate = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={"title": "Future League"},
            event_markets={"h2h", "to_qualify"},
        )
        view = settlement_training_views([settlement, certificate])[0]
        self.assertFalse(settlement_training_admissible(view))
        self.assertEqual(
            certificate["classification_basis"],
            "EVENT_TO_QUALIFY_MARKET_PRESENT",
        )

    def test_certificate_recomputes_classification_basis_and_rejects_tampering(self) -> None:
        settlement = build_settlement(
            completed_score(),
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=False,
        )
        certificate = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={"title": "Future League"},
            event_markets={"h2h", "totals"},
        )
        tampered = {**certificate, "classification_basis": "TRUST_ME"}
        self.assertTrue(
            settlement_admissibility_certificate_valid(settlement, certificate)
        )
        self.assertFalse(
            settlement_admissibility_certificate_valid(settlement, tampered)
        )

    def test_identical_evidence_has_stable_immutable_key(self) -> None:
        settlement = build_settlement(
            completed_score(),
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=False,
        )
        first = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={"title": "Future League"},
            event_markets={"h2h", "totals"},
        )
        retry = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:06:00Z",
            competition={"title": "Future League"},
            event_markets={"totals", "h2h"},
        )
        self.assertEqual(first["SK"], retry["SK"])
        self.assertEqual(
            first["classification_evidence_digest"],
            retry["classification_evidence_digest"],
        )
        self.assertNotEqual(first["certificate_digest"], retry["certificate_digest"])

    def test_latest_certificate_tie_break_is_deterministic(self) -> None:
        settlement = build_settlement(
            completed_score(),
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=False,
        )
        first = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={"title": "Future League"},
            event_markets={"h2h"},
        )
        second = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T12:01:00Z",
            competition={"title": "Future League"},
            event_markets={"h2h", "totals"},
        )
        view = settlement_training_views([settlement, first, second])[0]
        expected = max(
            (first, second), key=lambda row: row["certificate_digest"]
        )
        self.assertEqual(
            view["training_admissibility_certificate"]["certificate_digest"],
            expected["certificate_digest"],
        )

    def test_classification_only_score_transition_is_nonblocking(self) -> None:
        score = {
            **completed_score(),
            "sport_key": "soccer_uefa_champs_league",
        }
        existing = build_settlement(
            score,
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )
        candidate = build_settlement(
            score,
            observed_at="2026-08-14T12:01:00Z",
            regulation_ambiguous=False,
        )
        self.assertTrue(settlement_score_records_equivalent(existing, candidate))
        self.assertFalse(
            settlement_conflict_blocks_training(
                {
                    "training_blocked": True,
                    "existing": existing,
                    "candidate": candidate,
                }
            )
        )

    def test_reconciliation_certifies_existing_scores_without_provider_calls(self) -> None:
        score = {
            **completed_score(),
            "sport_key": "soccer_uefa_champs_league",
        }
        settlement = build_settlement(
            score,
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )

        class Store:
            settlements = object()

            def __init__(self):
                self.written = []

            def scan_all(self, table, **kwargs):
                self.assert_table = table
                return [settlement]

            def cumulative_market_inventory(self, event_key, *, observed_at):
                return {"book": {"markets": ["h2h", "totals"]}}

            def put_settlement(self, item):
                self.written.append(item)
                return True

        store = Store()
        result = reconcile_settlement_admissibility(
            store,
            observed_at="2026-08-14T12:02:00Z",
            competition_rows={
                "soccer_uefa_champs_league": {
                    "sport_key": "soccer_uefa_champs_league",
                    "title": "UEFA Champions League",
                }
            },
        )
        self.assertEqual(result["certificates_written"], 1)
        self.assertEqual(result["failures"], [])
        view = settlement_training_views([settlement, *store.written])[0]
        self.assertTrue(settlement_training_admissible(view))

    def test_reconciliation_prefers_bounded_checkpointed_page(self) -> None:
        score = {
            **completed_score(),
            "sport_key": "soccer_uefa_champs_league",
        }
        settlement = build_settlement(
            score,
            observed_at="2026-08-14T12:00:00Z",
            regulation_ambiguous=True,
        )

        class Store:
            def __init__(self):
                self.written = []
                self.checkpoint = None

            def claim_job(self, job_key, expires_at):
                self.claim = (job_key, expires_at)
                return True

            def settlement_admissibility_migration_page(self, *, limit):
                self.page_limit = limit
                return {
                    "rows": [settlement],
                    "cursor_digest": digest({}),
                    "next_start_key": {"PK": "next", "SK": "FINAL#v1"},
                    "cycle": 3,
                    "page_index": 4,
                }

            def checkpoint_settlement_admissibility_migration(self, **kwargs):
                self.checkpoint = kwargs
                return True

            def cumulative_market_inventory(self, event_key, *, observed_at):
                return {"book": {"markets": ["h2h", "totals"]}}

            def put_settlement(self, item):
                self.written.append(item)
                return True

            def scan_all(self, *args, **kwargs):
                raise AssertionError("production reconciliation must be bounded")

        store = Store()
        result = reconcile_settlement_admissibility(
            store,
            observed_at="2026-08-14T12:02:00Z",
            competition_rows={
                "soccer_uefa_champs_league": {
                    "sport_key": "soccer_uefa_champs_league",
                    "title": "UEFA Champions League",
                }
            },
        )
        self.assertEqual(result["scan_mode"], "BOUNDED_CHECKPOINTED")
        self.assertEqual(result["certificates_written"], 1)
        self.assertTrue(result["scan_checkpointed"])
        self.assertTrue(result["scan_has_more"])
        self.assertEqual(result["scan_cycle"], 3)
        self.assertEqual(result["scan_page_index"], 4)
        self.assertIsNotNone(store.checkpoint)
        self.assertEqual(store.checkpoint["cycle"], 3)
        self.assertEqual(store.checkpoint["page_index"], 4)

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
