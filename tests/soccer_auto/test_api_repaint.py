from __future__ import annotations

import inspect
import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.api import coverage, predictions  # noqa: E402
from soccer_auto.canonical import schedule_identity  # noqa: E402


class PredictionTable:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        return {"Items": list(self.rows)}


class OpsTable:
    def __init__(self, bindings):
        self.bindings = {
            (str(row["PK"]), str(row["SK"])): dict(row)
            for row in bindings
        }

    def get_item(self, **kwargs):
        key = kwargs["Key"]
        row = self.bindings.get((str(key["PK"]), str(key["SK"])))
        return {"Item": dict(row)} if row else {}


class Store:
    def __init__(self, rows, current, bindings):
        self.predictions = PredictionTable(rows)
        self.current = current
        self.ops = OpsTable(bindings)

    def get_event(self, event_key):
        return self.current.get(event_key)


def row(*, model: str, revision: int = 4, status: str = "PUBLISHED", created: str):
    value = {
        "event_key": "EVENT#soccer_test#one",
        "event_id": "one",
        "sport_key": "soccer_test",
        "schedule_revision": revision,
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "horizon": "T45",
        "target": "result_1x2",
        "feature_hash": "feature-four",
        "model_digest": model,
        "model_authority": "CHAMPION",
        "prediction_status": status,
        "created_at": created,
        "immutable": True,
    }
    value["schedule_identity"] = schedule_identity(value)
    return value


def current_event(*, revision: int = 4):
    value = {
        "event_key": "EVENT#soccer_test#one",
        "event_id": "one",
        "sport_key": "soccer_test",
        "schedule_revision": revision,
        "commence_time": "2026-08-14T14:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
    }
    value["schedule_identity"] = schedule_identity(value)
    return value


def binding(*, model: str, revision: int = 4):
    event = current_event(revision=revision)
    return {
        "PK": "PUBLIC_PREDICTION_BINDING#EVENT#soccer_test#one",
        "SK": f"REV#{revision}#HORIZON#T45#TARGET#result_1x2",
        "entity_type": "SOCCER_PUBLIC_PREDICTION_BINDING",
        "binding_version": "soccer-auto-public-prediction-binding-v1",
        "event_key": event["event_key"],
        "event_id": event["event_id"],
        "sport_key": event["sport_key"],
        "commence_time": event["commence_time"],
        "schedule_revision": revision,
        "schedule_identity": event["schedule_identity"],
        "horizon": "T45",
        "target": "result_1x2",
        "lock_sk": f"LOCK#T45#REV#{revision}#TARGET#result_1x2",
        "feature_hash": "feature-four",
        "model_digest": model,
        "immutable": True,
    }


class ApiRepaintTests(unittest.TestCase):
    def test_public_endpoint_suppresses_shadow_stale_and_duplicate_authorities(self):
        rows = [
            row(model="shadow", status="SHADOW", created="2026-08-14T13:15:00Z"),
            row(model="old-revision", revision=3, created="2026-08-14T13:14:00Z"),
            row(model="first-bound", created="2026-08-14T13:15:01Z"),
            row(model="later-repaint", created="2026-08-14T13:16:01Z"),
        ]
        current = {"EVENT#soccer_test#one": current_event()}
        result = predictions(Store(rows, current, [binding(model="first-bound")]))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["predictions"][0]["model_digest"], "first-bound")
        self.assertEqual(result["audit_rows_suppressed"], 3)

    def test_public_endpoint_requires_champion_current_identity_and_exact_binding(self):
        valid = row(model="bound", created="2026-08-14T13:15:01Z")
        challenger = {**valid, "model_digest": "challenger", "model_authority": "PROSPECTIVE_SHADOW"}
        copied_identity = {
            **valid,
            "model_digest": "copied-identity",
            "home_team": "Repainted Home",
        }
        wrong_model = {**valid, "model_digest": "not-bound"}
        missing_binding = row(model="missing-binding", revision=5, created="2026-08-14T13:17:00Z")
        current = {"EVENT#soccer_test#one": current_event()}

        result = predictions(
            Store(
                [challenger, copied_identity, wrong_model, missing_binding, valid],
                current,
                [binding(model="bound")],
            )
        )

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["predictions"][0]["model_digest"], "bound")
        self.assertEqual(result["audit_rows_suppressed"], 4)

    def test_public_endpoint_fails_closed_without_immutable_binding(self):
        decision = row(model="bound", created="2026-08-14T13:15:01Z")
        current = {"EVENT#soccer_test#one": current_event()}
        mutable = {**binding(model="bound"), "immutable": False}

        missing = predictions(Store([decision], current, []))
        mutable_result = predictions(Store([decision], current, [mutable]))

        self.assertEqual(missing["count"], 0)
        self.assertEqual(mutable_result["count"], 0)

    def test_coverage_hot_path_has_no_unbounded_scan_all(self):
        source = inspect.getsource(coverage)
        self.assertNotIn("scan_all", source)
        self.assertIn("_bounded_ops_diagnostics", source)


if __name__ == "__main__":
    unittest.main()
