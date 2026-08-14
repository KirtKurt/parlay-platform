from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402

from soccer_auto.canonical import digest, parse_utc, schedule_identity  # noqa: E402
from soccer_auto.inference import (  # noqa: E402
    build_frozen_lock,
    freeze_handler,
    predict_lock,
)


EVENT_KEY = "EVENT#soccer_test#event-id"


def event_row(*, revision: int = 4, commence_time: str = "2026-08-14T14:00:00Z"):
    return {
        "event_key": EVENT_KEY,
        "event_id": "event-id",
        "sport_key": "soccer_test",
        "commence_time": commence_time,
        "schedule_revision": revision,
        "metadata_revision": 12,
        "home_team": "Home",
        "away_team": "Away",
    }


def eligible_lock(*, revision: int = 4):
    event = event_row(revision=revision)
    features = {
        "feature_names": ["signal"],
        "values": [1.0],
        "market_prior": [0.34, 0.33, 0.33],
    }
    required_pairs = ["book|h2h"]
    source_hashes = ["a" * 64]
    certificate_digest = "b" * 64
    lock = {
        "PK": EVENT_KEY,
        "SK": f"LOCK#T45#REV#{revision}#TARGET#result_1x2",
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        "lock_version": "soccer-auto-t45-lock-v2",
        **event,
        "schedule_identity": schedule_identity(event),
        "target": "result_1x2",
        "lock_at": "2026-08-14T13:15:00Z",
        "feature_schema_version": "schema-v1",
        "prediction_eligible": True,
        "training_eligible": True,
        "frozen_features": features,
        "coverage_certificate_version": "soccer-auto-coverage-certificate-v2",
        "coverage_certificate_digest": certificate_digest,
        "coverage_plan_digest": "plan-digest",
        "coverage_plan_observed_at": "2026-08-14T13:10:00Z",
        "coverage_completed_at": "2026-08-14T13:14:00Z",
        "coverage_required_pairs": required_pairs,
        "coverage_required_pair_count": len(required_pairs),
        "coverage_required_pair_digest": digest(required_pairs),
        "coverage_probe_pairs": [],
        "coverage_probe_pair_count": 0,
        "coverage_probe_pair_digest": digest([]),
        "coverage_completed_before_lock": True,
        "movement_baseline_certificate_digest": certificate_digest,
        "movement_baseline_plan_digest": "plan-digest",
        "movement_baseline_plan_observed_at": "2026-08-14T13:10:00Z",
        "movement_baseline_distinct": False,
        "source_slot_ids": ["slot-one"],
        "source_payload_hashes": source_hashes,
        "source_raw_uris": ["s3://raw/slot-one.json"],
        "source_observed_at_max": "2026-08-14T13:14:00Z",
        "source_observed_before_lock": True,
        "movement_baseline_source_slot_ids": ["slot-one"],
        "movement_baseline_source_payload_hashes": source_hashes,
        "movement_baseline_source_raw_uris": ["s3://raw/slot-one.json"],
        "movement_baseline_source_observed_at_max": "2026-08-14T13:14:00Z",
        "movement_baseline_source_observed_before_lock": True,
        "labels": None,
        "immutable": True,
    }
    lock["feature_hash"] = digest(
        {
            "event_key": EVENT_KEY,
            "schedule_revision": revision,
            "lock_at": lock["lock_at"],
            "coverage_certificate_digest": certificate_digest,
            "coverage_plan_digest": lock["coverage_plan_digest"],
            "coverage_plan_observed_at": lock[
                "coverage_plan_observed_at"
            ],
            "coverage_completed_at": lock["coverage_completed_at"],
            "coverage_required_pairs": required_pairs,
            "coverage_probe_pairs": [],
            "movement_baseline_certificate_digest": certificate_digest,
            "movement_baseline_plan_digest": lock[
                "movement_baseline_plan_digest"
            ],
            "movement_baseline_plan_observed_at": lock[
                "movement_baseline_plan_observed_at"
            ],
            "source_slot_ids": lock["source_slot_ids"],
            "source_hashes": source_hashes,
            "source_raw_uris": lock["source_raw_uris"],
            "source_observed_at_max": lock["source_observed_at_max"],
            "movement_baseline_source_slot_ids": lock[
                "movement_baseline_source_slot_ids"
            ],
            "movement_baseline_source_hashes": source_hashes,
            "movement_baseline_source_raw_uris": lock[
                "movement_baseline_source_raw_uris"
            ],
            "movement_baseline_source_observed_at_max": lock[
                "movement_baseline_source_observed_at_max"
            ],
            "features": features,
        }
    )
    return lock


def predict_at(store, lock, observed_at: str):
    observed = parse_utc(observed_at)
    return predict_lock(
        store,
        lock,
        observed_at=observed_at,
        clock=lambda: observed,
    )


class OpsTable:
    def __init__(self, state):
        self.rows = {}
        if state:
            self.state = state

    @property
    def state(self):
        return self.rows.get(("AUTONOMY", "STATE"))

    @state.setter
    def state(self, value):
        normalized = dict(value)
        if normalized.get("updated_at"):
            normalized.setdefault(
                "updated_at_epoch_ms",
                int(parse_utc(normalized["updated_at"]).timestamp() * 1000),
            )
        self.rows[("AUTONOMY", "STATE")] = normalized

    def get_item(self, **kwargs):
        key = kwargs["Key"]
        row = self.rows.get((key["PK"], key["SK"]))
        return {"Item": row} if row else {}

    def put_item(self, **kwargs):
        item = kwargs["Item"]
        key = (item["PK"], item["SK"])
        if kwargs.get("ConditionExpression") and key in self.rows:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}},
                "PutItem",
            )
        self.rows[key] = item


class FakeModel:
    feature_names = ("signal",)

    def predict_proba(self, values, market_prior):
        return [0.70, 0.20, 0.10]


class PredictionStore:
    def __init__(self, current_event, autonomy):
        self.current_event = current_event
        self.ops = OpsTable(autonomy)
        self.predictions = {}

    def get_event(self, event_key):
        return self.current_event

    def put_prediction(self, item):
        key = (item["PK"], item["SK"])
        if key in self.predictions:
            return False
        self.predictions[key] = item
        return True

    def put_public_prediction(self, *, binding, prediction):
        binding_key = (binding["PK"], binding["SK"])
        prediction_key = (prediction["PK"], prediction["SK"])
        existing_binding = self.ops.rows.get(binding_key)
        existing_prediction = self.predictions.get(prediction_key)
        if existing_binding:
            if existing_binding["model_digest"] != binding["model_digest"]:
                return False, "PUBLIC_MODEL_BINDING_MISMATCH", existing_binding
            if existing_prediction:
                return False, "PUBLIC_PREDICTION_ALREADY_WRITTEN", existing_binding
            self.predictions[prediction_key] = prediction
            return True, "PUBLIC_PREDICTION_RECOVERED", existing_binding
        if existing_prediction:
            return False, "PUBLIC_PREDICTION_BINDING_MISSING", {}
        self.ops.rows[binding_key] = binding
        self.predictions[prediction_key] = prediction
        return True, "PUBLIC_PREDICTION_WRITTEN", binding


class LockBuildStore:
    def __init__(self):
        self.requested_revision = None
        self.requested_schedule_identity = None

    def coverage_certificates_before(
        self,
        event_key,
        cutoff,
        *,
        schedule_revision=None,
        schedule_identity=None,
    ):
        self.requested_revision = schedule_revision
        self.requested_schedule_identity = schedule_identity
        return []

    def canonical_slots_before(
        self,
        event_key,
        cutoff,
        *,
        schedule_revision=None,
        schedule_identity=None,
    ):
        self.requested_revision = schedule_revision
        self.requested_schedule_identity = schedule_identity
        return []


class FreezeStore(PredictionStore):
    def __init__(self, lock):
        super().__init__(
            event_row(revision=lock["schedule_revision"]),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:15:00Z",
            },
        )
        self.lock = lock
        self.put_lock_called = False

    def active_events_between(self, start, end):
        return [self.current_event]

    def get_lock(self, event_key, target="result_1x2", *, schedule_revision=None):
        if schedule_revision == self.lock["schedule_revision"]:
            return self.lock
        return None

    def put_lock(self, item):
        self.put_lock_called = True
        return True


CHAMPION = {
    "SK": "CHAMPION",
    "model_digest": "champion-digest",
    "authority_state": "CHAMPION",
}
SHADOW = {
    "SK": "VERSION#shadow",
    "model_digest": "shadow-digest",
    "authority_state": "PROSPECTIVE_SHADOW",
}
NEW_CHAMPION = {
    "SK": "CHAMPION",
    "model_digest": "new-champion-digest",
    "authority_state": "CHAMPION",
}


class InferenceSafetyTests(unittest.TestCase):
    def test_autonomy_blocks_champion_but_shadow_continues_and_champion_can_retry(self):
        store = PredictionStore(
            event_row(),
            {
                "authority": "DEGRADED",
                "automatic_prediction_allowed": False,
                "promotion_blocked": True,
            },
        )
        with patch("soccer_auto.inference._active_models", return_value=[CHAMPION, SHADOW]), patch(
            "soccer_auto.inference._load_model", return_value=FakeModel()
        ):
            first = predict_at(store, eligible_lock(), "2026-08-14T13:15:01Z")
            self.assertEqual(first["predictions"], 1)
            self.assertEqual(first["blocked"][0]["reason"], "AUTONOMY_PUBLISH_NOT_ALLOWED")
            self.assertEqual(
                [row["prediction_status"] for row in store.predictions.values()],
                ["SHADOW"],
            )

            store.ops.state = {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:16:00Z",
            }
            second = predict_at(store, eligible_lock(), "2026-08-14T13:16:01Z")

        self.assertEqual(second["predictions"], 1)
        champion = next(
            row for row in store.predictions.values() if row["model_digest"] == "champion-digest"
        )
        self.assertEqual(champion["prediction_status"], "PUBLISHED")
        self.assertIn("#REV#4#", champion["SK"])

    def test_existing_current_revision_lock_is_retried_by_freeze_cycle(self):
        store = FreezeStore(eligible_lock())
        observed = datetime(2026, 8, 14, 13, 16, tzinfo=timezone.utc)
        with patch("soccer_auto.inference.SoccerStore", return_value=store), patch(
            "soccer_auto.inference.now_utc", return_value=observed
        ), patch("soccer_auto.inference._active_models", return_value=[CHAMPION]), patch(
            "soccer_auto.inference._load_model", return_value=FakeModel()
        ):
            result = freeze_handler({}, None)

        self.assertEqual(result["locks_created"], 0)
        self.assertEqual(result["locks_retried"], 1)
        self.assertEqual(result["predictions_written"], 1)
        self.assertFalse(store.put_lock_called)

    def test_revision_versions_lock_and_stale_lock_cannot_infer(self):
        build_store = LockBuildStore()
        lock = build_frozen_lock(
            build_store,
            event_row(revision=7),
            observed_at="2026-08-14T13:15:00Z",
        )
        self.assertEqual(lock["schedule_revision"], 7)
        self.assertIn("#REV#7#", lock["SK"])
        self.assertEqual(build_store.requested_revision, 7)
        self.assertEqual(lock["schedule_identity"], schedule_identity(event_row(revision=7)))
        self.assertEqual(
            build_store.requested_schedule_identity,
            lock["schedule_identity"],
        )

        prediction_store = PredictionStore(event_row(revision=8), autonomy={})
        result = predict_at(
            prediction_store,
            eligible_lock(revision=7),
            "2026-08-14T13:15:01Z",
        )
        self.assertEqual(result["reason"], "STALE_SCHEDULE_IDENTITY")
        self.assertEqual(prediction_store.predictions, {})

    def test_first_public_champion_binding_prevents_later_champion_repaint(self):
        store = PredictionStore(
            event_row(),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:15:00Z",
            },
        )
        with patch("soccer_auto.inference._load_model", return_value=FakeModel()):
            with patch("soccer_auto.inference._active_models", return_value=[CHAMPION, SHADOW]):
                first = predict_at(
                    store,
                    eligible_lock(),
                    "2026-08-14T13:15:01Z",
                )
            with patch("soccer_auto.inference._active_models", return_value=[CHAMPION]):
                same_model_retry = predict_at(
                    store,
                    eligible_lock(),
                    "2026-08-14T13:15:30Z",
                )
            with patch(
                "soccer_auto.inference._active_models",
                return_value=[NEW_CHAMPION, SHADOW],
            ):
                second = predict_at(
                    store,
                    eligible_lock(),
                    "2026-08-14T13:16:01Z",
                )

        published = [
            row
            for row in store.predictions.values()
            if row["prediction_status"] == "PUBLISHED"
        ]
        self.assertEqual(first["predictions"], 2)
        self.assertEqual(same_model_retry["predictions"], 0)
        self.assertEqual(same_model_retry["blocked"], [])
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["model_digest"], "champion-digest")
        self.assertFalse(
            any(
                row["model_digest"] == "new-champion-digest"
                for row in store.predictions.values()
            )
        )
        self.assertEqual(
            second["blocked"][0]["reason"],
            "PUBLIC_MODEL_BINDING_MISMATCH",
        )
        binding_rows = [
            row
            for row in store.ops.rows.values()
            if row.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
        ]
        self.assertEqual(len(binding_rows), 1)
        self.assertEqual(binding_rows[0]["model_digest"], "champion-digest")

    def test_all_prediction_writes_respect_commit_headroom_and_t10(self):
        autonomy = {
            "authority": "AUTHORITATIVE",
            "automatic_prediction_allowed": True,
            "promotion_blocked": False,
            "updated_at": "2026-08-14T13:49:00Z",
        }
        with patch("soccer_auto.inference._active_models", return_value=[CHAMPION, SHADOW]), patch(
            "soccer_auto.inference._load_model", return_value=FakeModel()
        ):
            at_commit_deadline = PredictionStore(event_row(), autonomy)
            allowed = predict_at(
                at_commit_deadline,
                eligible_lock(),
                "2026-08-14T13:49:50Z",
            )
            inside_headroom = PredictionStore(event_row(), autonomy)
            headroom_blocked = predict_at(
                inside_headroom,
                eligible_lock(),
                "2026-08-14T13:49:50.001000Z",
            )
            after_cutoff = PredictionStore(event_row(), autonomy)
            rejected = predict_at(
                after_cutoff,
                eligible_lock(),
                "2026-08-14T13:50:00.001000Z",
            )

        self.assertEqual(allowed["predictions"], 2)
        self.assertEqual(
            sum(
                row["prediction_status"] == "PUBLISHED"
                for row in at_commit_deadline.predictions.values()
            ),
            1,
        )
        self.assertEqual(headroom_blocked["predictions"], 0)
        self.assertEqual(inside_headroom.predictions, {})
        self.assertTrue(
            all(
                row["reason"] == "PUBLICATION_COMMIT_HEADROOM_EXCEEDED"
                for row in headroom_blocked["blocked"]
            )
        )
        self.assertEqual(rejected["predictions"], 0)
        self.assertEqual(after_cutoff.predictions, {})
        self.assertEqual(
            {row["model_authority"] for row in rejected["blocked"]},
            {"CHAMPION", "PROSPECTIVE_SHADOW"},
        )
        self.assertTrue(
            all(
                row["reason"] == "PUBLICATION_AFTER_T10_CUTOFF"
                for row in rejected["blocked"]
            )
        )

    def test_final_wall_clock_check_blocks_work_that_crosses_t10(self):
        autonomy = {
            "authority": "AUTHORITATIVE",
            "automatic_prediction_allowed": True,
            "promotion_blocked": False,
            "updated_at": "2026-08-14T13:49:00Z",
        }

        class SequenceClock:
            def __init__(self, *values: str):
                self.values = [parse_utc(value) for value in values]

            def __call__(self):
                return self.values.pop(0)

        with patch("soccer_auto.inference._load_model", return_value=FakeModel()):
            shadow_store = PredictionStore(event_row(), autonomy)
            with patch("soccer_auto.inference._active_models", return_value=[SHADOW]):
                shadow_result = predict_lock(
                    shadow_store,
                    eligible_lock(),
                    observed_at="2026-08-14T13:49:00Z",
                    clock=SequenceClock(
                        "2026-08-14T13:49:49Z",
                        "2026-08-14T13:49:50.001000Z",
                    ),
                )

            champion_store = PredictionStore(event_row(), autonomy)
            with patch("soccer_auto.inference._active_models", return_value=[CHAMPION]):
                champion_result = predict_lock(
                    champion_store,
                    eligible_lock(),
                    observed_at="2026-08-14T13:49:00Z",
                    clock=SequenceClock(
                        "2026-08-14T13:49:49Z",
                        "2026-08-14T13:49:49.500000Z",
                        "2026-08-14T13:49:50.001000Z",
                    ),
                )

        self.assertEqual(shadow_result["predictions"], 0)
        self.assertEqual(champion_result["predictions"], 0)
        self.assertEqual(shadow_store.predictions, {})
        self.assertEqual(champion_store.predictions, {})
        self.assertEqual(
            shadow_result["blocked"][0]["reason"],
            "PUBLICATION_COMMIT_HEADROOM_EXCEEDED",
        )
        self.assertEqual(
            champion_result["blocked"][0]["reason"],
            "PUBLICATION_COMMIT_HEADROOM_EXCEEDED",
        )
        self.assertFalse(
            any(
                row.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
                for row in champion_store.ops.rows.values()
            )
        )

    def test_failed_atomic_public_write_leaves_no_binding_or_prediction(self):
        class FailedAtomicStore(PredictionStore):
            def put_public_prediction(self, *, binding, prediction):
                return False, "PUBLIC_MODEL_BINDING_UNAVAILABLE", {}

        store = FailedAtomicStore(
            event_row(),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:19:00Z",
            },
        )
        with patch("soccer_auto.inference._active_models", return_value=[CHAMPION]), patch(
            "soccer_auto.inference._load_model", return_value=FakeModel()
        ):
            result = predict_at(store, eligible_lock(), "2026-08-14T13:20:00Z")

        self.assertEqual(result["predictions"], 0)
        self.assertEqual(result["blocked"][0]["reason"], "PUBLIC_MODEL_BINDING_UNAVAILABLE")
        self.assertEqual(store.predictions, {})
        self.assertFalse(
            any(
                row.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
                for row in store.ops.rows.values()
            )
        )

    def test_prediction_and_binding_timestamps_use_actual_write_clock(self):
        store = PredictionStore(
            event_row(),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:19:00Z",
            },
        )
        actual = parse_utc("2026-08-14T13:20:00Z")
        with patch("soccer_auto.inference._active_models", return_value=[CHAMPION]), patch(
            "soccer_auto.inference._load_model", return_value=FakeModel()
        ):
            result = predict_lock(
                store,
                eligible_lock(),
                observed_at="2026-08-14T13:15:00Z",
                clock=lambda: actual,
            )

        self.assertEqual(result["predictions"], 1)
        prediction = next(iter(store.predictions.values()))
        self.assertEqual(prediction["created_at"], "2026-08-14T13:20:00Z")
        binding = next(
            row
            for row in store.ops.rows.values()
            if row.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
        )
        self.assertEqual(binding["bound_at"], "2026-08-14T13:20:00Z")
        self.assertEqual(binding["event_metadata_revision"], 12)
        self.assertEqual(
            binding["autonomy_updated_at_epoch_ms"],
            int(parse_utc("2026-08-14T13:19:00Z").timestamp() * 1000),
        )

    def test_stale_authority_state_blocks_champion_but_not_timely_shadow(self):
        store = PredictionStore(
            event_row(),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
                "updated_at": "2026-08-14T13:00:00Z",
            },
        )
        with patch(
            "soccer_auto.inference._active_models",
            return_value=[CHAMPION, SHADOW],
        ), patch("soccer_auto.inference._load_model", return_value=FakeModel()):
            result = predict_at(store, eligible_lock(), "2026-08-14T13:31:00Z")

        self.assertEqual(result["predictions"], 1)
        self.assertEqual(result["blocked"][0]["reason"], "AUTONOMY_STATE_STALE")
        self.assertEqual(
            [row["prediction_status"] for row in store.predictions.values()],
            ["SHADOW"],
        )

    def test_same_revision_and_kickoff_with_changed_team_is_stale_identity(self):
        changed = event_row()
        changed["home_team"] = "Replacement Home"
        store = PredictionStore(changed, autonomy={})

        result = predict_at(
            store,
            eligible_lock(),
            "2026-08-14T13:15:01Z",
        )

        self.assertEqual(result["reason"], "STALE_SCHEDULE_IDENTITY")
        self.assertEqual(store.predictions, {})


if __name__ == "__main__":
    unittest.main()
