from __future__ import annotations

import unittest
from datetime import datetime, timezone

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import digest, schedule_identity  # noqa: E402
from soccer_auto.health import (  # noqa: E402
    HEALTH_CONTRACT_VERSION,
    prediction_and_training_health,
)
from soccer_auto.historical_materializer import _build_lock  # noqa: E402
from soccer_auto.inference import _public_model_binding  # noqa: E402
from soccer_auto.market_features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION  # noqa: E402
from soccer_auto.settlement import (  # noqa: E402
    build_settlement,
    build_settlement_admissibility_certificate,
    settlement_training_views,
)
from tests.soccer_auto.test_inference_safety import (  # noqa: E402
    eligible_lock,
    eligible_training_lock,
    event_row,
)


class Table:
    def __init__(self, rows=None, *, truncated=False):
        self.rows = [dict(row) for row in (rows or [])]
        self.truncated = truncated

    def scan(self, **kwargs):
        result = {"Items": [dict(row) for row in self.rows]}
        if self.truncated:
            result["LastEvaluatedKey"] = {"PK": "more", "SK": "more"}
        return result


class Ops(Table):
    def __init__(self, bindings=None, conflicts=None, *, truncated=False):
        super().__init__(conflicts or [], truncated=truncated)
        self.bindings = {
            (str(row["PK"]), str(row["SK"])): dict(row)
            for row in (bindings or [])
        }

    def get_item(self, *, Key, **kwargs):
        row = self.bindings.get((str(Key["PK"]), str(Key["SK"])))
        return {"Item": dict(row)} if row else {}

    def query(self, **kwargs):
        result = {"Items": [dict(row) for row in self.rows]}
        if self.truncated:
            result["LastEvaluatedKey"] = {"PK": "more", "SK": "more"}
        return result


class Store:
    def __init__(
        self,
        *,
        events,
        locks,
        settlements=None,
        predictions=None,
        bindings=None,
        conflicts=None,
        truncated_table: str | None = None,
    ):
        self.events = Table(events, truncated=truncated_table == "events")
        self.locks = Table(locks, truncated=truncated_table == "locks")
        self.settlements = Table(
            settlements or [], truncated=truncated_table == "settlements"
        )
        self.predictions = Table(
            predictions or [], truncated=truncated_table == "predictions"
        )
        self.ops = Ops(
            bindings,
            conflicts,
            truncated=truncated_table == "ops",
        )
        self.event_index = {
            str(row.get("event_key") or row.get("PK") or ""): dict(row)
            for row in events
        }

    def get_event(self, event_key):
        return self.event_index.get(event_key)


def current_event(*, sport_key="soccer_test", event_id="event-id"):
    event = {
        **event_row(),
        "PK": f"EVENT#{sport_key}#{event_id}",
        "SK": "METADATA",
        "event_key": f"EVENT#{sport_key}#{event_id}",
        "event_id": event_id,
        "sport_key": sport_key,
        "entity_type": "SOCCER_EVENT",
        "completed": False,
        "first_seen_at": "2026-08-14T00:00:00Z",
    }
    event["schedule_identity"] = schedule_identity(event)
    return event


def _recompute_lock_hash(lock):
    features = lock["frozen_features"]
    lock["feature_hash"] = digest(
        {
            "event_key": lock["event_key"],
            "schedule_revision": int(lock["schedule_revision"]),
            "lock_at": lock["lock_at"],
            "coverage_certificate_digest": lock[
                "coverage_certificate_digest"
            ],
            "coverage_plan_digest": lock["coverage_plan_digest"],
            "coverage_plan_observed_at": lock[
                "coverage_plan_observed_at"
            ],
            "coverage_completed_at": lock["coverage_completed_at"],
            "coverage_required_pairs": sorted(
                lock.get("coverage_required_pairs") or []
            ),
            "coverage_probe_pairs": sorted(
                lock.get("coverage_probe_pairs") or []
            ),
            "movement_baseline_certificate_digest": lock[
                "movement_baseline_certificate_digest"
            ],
            "movement_baseline_plan_digest": lock[
                "movement_baseline_plan_digest"
            ],
            "movement_baseline_plan_observed_at": lock[
                "movement_baseline_plan_observed_at"
            ],
            "source_slot_ids": list(lock["source_slot_ids"]),
            "source_hashes": list(lock["source_payload_hashes"]),
            "source_raw_uris": list(lock["source_raw_uris"]),
            "source_observed_at_max": lock["source_observed_at_max"],
            "movement_baseline_source_slot_ids": list(
                lock["movement_baseline_source_slot_ids"]
            ),
            "movement_baseline_source_hashes": list(
                lock["movement_baseline_source_payload_hashes"]
            ),
            "movement_baseline_source_raw_uris": list(
                lock["movement_baseline_source_raw_uris"]
            ),
            "movement_baseline_source_observed_at_max": lock[
                "movement_baseline_source_observed_at_max"
            ],
            "features": features,
        }
    )
    return lock


def bind_lock_to_event(lock, event):
    lock = dict(lock)
    for field in (
        "event_key",
        "event_id",
        "sport_key",
        "commence_time",
        "schedule_revision",
        "home_team",
        "away_team",
    ):
        lock[field] = event[field]
    lock["PK"] = event["event_key"]
    lock["schedule_identity"] = event["schedule_identity"]
    return _recompute_lock_hash(lock)


def full_training_lock(event):
    lock = bind_lock_to_event(eligible_training_lock(), event)
    lock["feature_schema_version"] = FEATURE_SCHEMA_VERSION
    lock["frozen_features"] = {
        "feature_names": list(FEATURE_NAMES),
        "values": [0.0] * len(FEATURE_NAMES),
        "market_prior": [0.34, 0.33, 0.33],
    }
    return _recompute_lock_hash(lock)


def public_evidence(lock, *, created_at="2026-08-14T13:49:20Z"):
    prediction = {
        "PK": lock["event_key"],
        "SK": (
            f"PRED#T10#REV#{lock['schedule_revision']}#TARGET#result_1x2#"
            "MODEL#champion"
        ),
        "entity_type": "SOCCER_MODEL_PREDICTION",
        "event_key": lock["event_key"],
        "event_id": lock["event_id"],
        "sport_key": lock["sport_key"],
        "commence_time": lock["commence_time"],
        "schedule_revision": lock["schedule_revision"],
        "schedule_identity": lock["schedule_identity"],
        "home_team": lock["home_team"],
        "away_team": lock["away_team"],
        "target": "result_1x2",
        "horizon": "T10",
        "lock_at": lock["lock_at"],
        "decision_target_at": lock["decision_target_at"],
        "capture_opens_at": lock["capture_opens_at"],
        "lock_commit_deadline": lock["lock_commit_deadline"],
        "source_observed_at_max": lock["source_observed_at_max"],
        "publication_cutoff": lock["decision_target_at"],
        "commit_deadline": lock["lock_commit_deadline"],
        "commit_headroom_seconds": 10.0,
        "feature_hash": lock["feature_hash"],
        "feature_schema_version": lock["feature_schema_version"],
        "lock_version": lock["lock_version"],
        "coverage_certificate_version": lock[
            "coverage_certificate_version"
        ],
        "coverage_certificate_digest": lock[
            "coverage_certificate_digest"
        ],
        "coverage_plan_digest": lock["coverage_plan_digest"],
        "model_digest": "champion",
        "model_authority": "CHAMPION",
        "prediction_status": "PUBLISHED",
        "selection": "home",
        "highest_probability_outcome": "home",
        "created_at": created_at,
        "autonomy_updated_at": "2026-08-14T13:49:00Z",
        "autonomy_updated_at_epoch_ms": 1_786_715_340_000,
        "event_metadata_revision": 12,
        "immutable": True,
    }
    binding = _public_model_binding(lock, model_digest="champion")
    binding.update(
        {
            "publication_cutoff": lock["decision_target_at"],
            "commit_deadline": lock["lock_commit_deadline"],
            "commit_headroom_seconds": 10.0,
            "bound_at": created_at,
            "autonomy_updated_at": "2026-08-14T13:49:00Z",
            "autonomy_updated_at_epoch_ms": 1_786_715_340_000,
            "event_metadata_revision": 12,
        }
    )
    return prediction, binding


class HealthContractTests(unittest.TestCase):
    observed = datetime(2026, 8, 14, 13, 49, 30, tzinfo=timezone.utc)

    def test_valid_t10_lock_and_binding_form_a_complete_health_proof(self):
        event = current_event()
        lock = bind_lock_to_event(eligible_lock(), event)
        prediction, binding = public_evidence(lock)
        result = prediction_and_training_health(
            Store(
                events=[event],
                locks=[lock],
                predictions=[prediction],
                bindings=[binding],
            ),
            observed=self.observed,
        )
        self.assertEqual(result["contract_version"], HEALTH_CONTRACT_VERSION)
        self.assertTrue(result["healthy"])
        self.assertTrue(result["proof_complete"])
        self.assertEqual(result["integrity_failures"], 0)
        self.assertEqual(result["t10_decisions"]["invalid_locks"], 0)
        self.assertEqual(result["public_authority"]["t10_public_rows"], 1)
        self.assertEqual(
            result["public_authority"]["binding_integrity_failures"], 0
        )

    def test_legacy_t45_public_row_is_visible_only_as_suppressed_audit(self):
        event = current_event()
        lock = bind_lock_to_event(eligible_lock(), event)
        prediction, binding = public_evidence(lock)
        legacy = {
            **prediction,
            "SK": "PRED#T45#REV#4#TARGET#result_1x2#MODEL#legacy",
            "horizon": "T45",
            "lock_version": "soccer-auto-t45-lock-v2",
            "model_digest": "legacy",
        }
        result = prediction_and_training_health(
            Store(
                events=[event],
                locks=[lock],
                predictions=[prediction, legacy],
                bindings=[binding],
            ),
            observed=self.observed,
        )
        self.assertEqual(result["public_authority"]["t10_public_rows"], 1)
        self.assertEqual(
            result["public_authority"]["legacy_t45_rows_suppressed"], 1
        )
        self.assertEqual(result["integrity_failures"], 0)

    def test_late_t10_prediction_is_an_integrity_failure(self):
        event = current_event()
        lock = bind_lock_to_event(eligible_lock(), event)
        prediction, binding = public_evidence(
            lock, created_at="2026-08-14T13:49:55Z"
        )
        result = prediction_and_training_health(
            Store(
                events=[event],
                locks=[lock],
                predictions=[prediction],
                bindings=[binding],
            ),
            observed=self.observed,
        )
        self.assertFalse(result["healthy"])
        self.assertEqual(result["state"], "DEGRADED_INTEGRITY")
        self.assertEqual(
            result["public_authority"][
                "public_prediction_after_commit_deadline"
            ],
            1,
        )
        self.assertGreaterEqual(result["integrity_failures"], 1)

    def test_certificate_and_t45_lock_produce_a_ready_training_row(self):
        event = current_event(
            sport_key="soccer_uefa_champs_league",
            event_id="event-id",
        )
        t10 = bind_lock_to_event(eligible_lock(), event)
        t45 = full_training_lock(event)
        score = {
            "id": event["event_id"],
            "sport_key": event["sport_key"],
            "schedule_revision": event["schedule_revision"],
            "schedule_identity": event["schedule_identity"],
            "commence_time": event["commence_time"],
            "completed": True,
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "scores": [
                {"name": event["home_team"], "score": "2"},
                {"name": event["away_team"], "score": "1"},
            ],
        }
        settlement = build_settlement(
            score,
            observed_at="2026-08-14T16:00:00Z",
            regulation_ambiguous=True,
        )
        certificate = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T16:01:00Z",
            competition={
                "sport_key": event["sport_key"],
                "title": "UEFA Champions League",
            },
            event_markets={"h2h", "totals"},
        )
        result = prediction_and_training_health(
            Store(
                events=[event],
                locks=[t10, t45],
                settlements=[settlement, certificate],
            ),
            observed=self.observed,
        )
        self.assertEqual(result["training"]["admissible_final_score_rows"], 1)
        self.assertEqual(result["training"]["admissibility_certificates"], 1)
        self.assertEqual(result["training"]["training_rows_ready"], 1)
        self.assertEqual(result["training"]["conversion_backlog"], 0)

    def test_historical_lock_recovers_legacy_nontraining_live_lock(self):
        event = current_event(
            sport_key="soccer_uefa_champs_league",
            event_id="historical-recovery",
        )
        t10 = bind_lock_to_event(eligible_lock(), event)
        legacy_live = full_training_lock(event)
        legacy_live["training_eligible"] = False
        score = {
            "id": event["event_id"],
            "sport_key": event["sport_key"],
            "schedule_revision": event["schedule_revision"],
            "schedule_identity": event["schedule_identity"],
            "commence_time": event["commence_time"],
            "completed": True,
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "scores": [
                {"name": event["home_team"], "score": "2"},
                {"name": event["away_team"], "score": "1"},
            ],
        }
        settlement = build_settlement(
            score,
            observed_at="2026-08-14T16:00:00Z",
            regulation_ambiguous=True,
        )
        certificate = build_settlement_admissibility_certificate(
            settlement,
            observed_at="2026-08-14T16:01:00Z",
            competition={
                "sport_key": event["sport_key"],
                "title": "UEFA Champions League",
            },
            event_markets={"h2h", "totals"},
        )
        effective = settlement_training_views([settlement, certificate])[0]
        historical_event = {
            "id": event["event_id"],
            "sport_key": event["sport_key"],
            "commence_time": event["commence_time"],
            "schedule_revision": event["schedule_revision"],
            "schedule_identity": event["schedule_identity"],
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "bookmakers": [
                {
                    "key": f"book-{index}",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": event["home_team"], "price": 2.1},
                                {"name": "Draw", "price": 3.2},
                                {"name": event["away_team"], "price": 3.6},
                            ],
                        }
                    ],
                }
                for index in range(3)
            ],
        }
        historical = _build_lock(
            effective,
            historical_event,
            provider_at="2026-08-14T13:15:00Z",
            raw_uri="s3://raw/historical-recovery.json",
            payload_hash=digest(historical_event),
            observed_at="2026-08-14T16:02:00Z",
        )
        result = prediction_and_training_health(
            Store(
                events=[event],
                locks=[t10, legacy_live, historical],
                settlements=[settlement, certificate],
            ),
            observed=self.observed,
        )
        self.assertEqual(result["training"]["training_rows_ready"], 1)
        self.assertEqual(result["training"]["conversion_backlog"], 0)
        self.assertEqual(result["training"]["invalid_existing_locks"], 0)
        self.assertEqual(result["training"]["nontraining_live_locks"], 1)

    def test_tampered_t10_lock_is_reported_invalid(self):
        event = current_event()
        lock = bind_lock_to_event(eligible_lock(), event)
        lock["feature_hash"] = "tampered"
        result = prediction_and_training_health(
            Store(events=[event], locks=[lock]),
            observed=self.observed,
        )
        self.assertFalse(result["healthy"])
        self.assertEqual(result["t10_decisions"]["invalid_locks"], 1)
        self.assertGreaterEqual(result["integrity_failures"], 1)

    def test_truncated_scan_can_never_report_healthy(self):
        event = current_event()
        lock = bind_lock_to_event(eligible_lock(), event)
        result = prediction_and_training_health(
            Store(events=[event], locks=[lock], truncated_table="events"),
            observed=self.observed,
        )
        self.assertTrue(result["scan_truncated"])
        self.assertFalse(result["proof_complete"])
        self.assertFalse(result["healthy"])
        self.assertEqual(result["state"], "INCOMPLETE_PROOF")


if __name__ == "__main__":
    unittest.main()
