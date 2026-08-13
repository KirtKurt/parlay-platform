from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

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
        "home_team": "Home",
        "away_team": "Away",
    }


def eligible_lock(*, revision: int = 4):
    return {
        "PK": EVENT_KEY,
        "SK": f"LOCK#T45#REV#{revision}#TARGET#result_1x2",
        "entity_type": "SOCCER_FROZEN_FEATURE_LOCK",
        **event_row(revision=revision),
        "target": "result_1x2",
        "lock_at": "2026-08-14T13:15:00Z",
        "feature_hash": "feature-hash",
        "feature_schema_version": "schema-v1",
        "prediction_eligible": True,
        "training_eligible": True,
        "frozen_features": {
            "feature_names": ["signal"],
            "values": [1.0],
            "market_prior": [0.34, 0.33, 0.33],
        },
    }


class OpsTable:
    def __init__(self, state):
        self.state = state

    def get_item(self, **kwargs):
        return {"Item": self.state} if self.state else {}


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


class LockBuildStore:
    def __init__(self):
        self.requested_revision = None

    def canonical_slots_before(self, event_key, cutoff, *, schedule_revision=None):
        self.requested_revision = schedule_revision
        return []


class FreezeStore(PredictionStore):
    def __init__(self, lock):
        super().__init__(
            event_row(revision=lock["schedule_revision"]),
            {
                "authority": "AUTHORITATIVE",
                "automatic_prediction_allowed": True,
                "promotion_blocked": False,
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
            first = predict_lock(store, eligible_lock(), observed_at="2026-08-14T13:15:01Z")
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
            }
            second = predict_lock(store, eligible_lock(), observed_at="2026-08-14T13:16:01Z")

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

        prediction_store = PredictionStore(event_row(revision=8), autonomy={})
        result = predict_lock(
            prediction_store,
            eligible_lock(revision=7),
            observed_at="2026-08-14T13:15:01Z",
        )
        self.assertEqual(result["reason"], "STALE_SCHEDULE_REVISION")
        self.assertEqual(prediction_store.predictions, {})


if __name__ == "__main__":
    unittest.main()
