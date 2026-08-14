from __future__ import annotations

import unittest
from decimal import Decimal

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402

from soccer_auto.storage import SoccerStore  # noqa: E402


def decode_attribute(value):
    if "S" in value:
        return value["S"]
    if "N" in value:
        number = Decimal(value["N"])
        return int(number) if number % 1 == 0 else number
    if "BOOL" in value:
        return value["BOOL"]
    if "NULL" in value:
        return None
    if "L" in value:
        return [decode_attribute(item) for item in value["L"]]
    if "M" in value:
        return {key: decode_attribute(item) for key, item in value["M"].items()}
    raise AssertionError(f"unsupported DynamoDB attribute: {value}")


def decode_item(value):
    return {key: decode_attribute(item) for key, item in value.items()}


class AtomicClient:
    def __init__(self):
        self.tables = {}
        self.calls = []

    def transact_write_items(self, **kwargs):
        self.calls.append(kwargs)
        changes = []
        reasons = []
        for operation in kwargs["TransactItems"]:
            if "Put" in operation:
                put = operation["Put"]
                table = self.tables[put["TableName"]]
                item = decode_item(put["Item"])
                key = (item["PK"], item["SK"])
                condition_failed = key in table.rows
                reasons.append(
                    {"Code": "ConditionalCheckFailed"}
                    if condition_failed
                    else {"Code": "None"}
                )
                changes.append((table, key, item))
                continue
            check = operation["ConditionCheck"]
            table = self.tables[check["TableName"]]
            key_item = decode_item(check["Key"])
            key = (key_item["PK"], key_item["SK"])
            existing = table.rows.get(key) or {}
            values = {
                name: decode_attribute(value)
                for name, value in check["ExpressionAttributeValues"].items()
            }
            names = check["ExpressionAttributeNames"]
            matches = bool(existing) and all(
                existing[field] == values[f":{field}"]
                for field in names.values()
            )
            reasons.append(
                {"Code": "None"}
                if matches
                else {"Code": "ConditionalCheckFailed"}
            )
        if any(reason["Code"] == "ConditionalCheckFailed" for reason in reasons):
            raise ClientError(
                {
                    "Error": {"Code": "TransactionCanceledException"},
                    "CancellationReasons": reasons,
                },
                "TransactWriteItems",
            )
        for table, key, item in changes:
            table.rows[key] = item


class AtomicTable:
    def __init__(self, name, client):
        self.name = name
        self.rows = {}
        self.meta = type("Meta", (), {"client": client})()
        client.tables[name] = self

    def get_item(self, *, Key, **kwargs):
        row = self.rows.get((Key["PK"], Key["SK"]))
        return {"Item": dict(row)} if row else {}


def binding(model_digest="model-one"):
    return {
        "PK": "PUBLIC_PREDICTION_BINDING#EVENT#soccer_test#event-id",
        "SK": "REV#4#HORIZON#T10#TARGET#result_1x2",
        "entity_type": "SOCCER_PUBLIC_PREDICTION_BINDING",
        "binding_version": "soccer-auto-public-prediction-binding-v3",
        "event_key": "EVENT#soccer_test#event-id",
        "event_id": "event-id",
        "sport_key": "soccer_test",
        "commence_time": "2026-08-14T14:00:00Z",
        "schedule_revision": 4,
        "schedule_identity": "schedule-identity",
        "horizon": "T10",
        "target": "result_1x2",
        "lock_sk": "LOCK#T10#REV#4#TARGET#result_1x2",
        "lock_version": "soccer-auto-t10-lock-v1",
        "lock_at": "2026-08-14T13:49:00Z",
        "decision_target_at": "2026-08-14T13:50:00Z",
        "capture_opens_at": "2026-08-14T13:48:20Z",
        "lock_commit_deadline": "2026-08-14T13:49:50Z",
        "source_observed_at_max": "2026-08-14T13:48:55Z",
        "feature_hash": "feature-hash",
        "coverage_certificate_version": "soccer-auto-coverage-certificate-v2",
        "coverage_certificate_digest": "c" * 64,
        "coverage_plan_digest": "plan-digest",
        "model_digest": model_digest,
        "bound_at": "2026-08-14T13:49:20Z",
        "publication_cutoff": "2026-08-14T13:50:00Z",
        "commit_deadline": "2026-08-14T13:49:50Z",
        "commit_headroom_seconds": 10,
        "autonomy_updated_at": "2026-08-14T13:49:00Z",
        "autonomy_updated_at_epoch_ms": 1_786_715_340_000,
        "event_metadata_revision": 12,
        "immutable": True,
    }


def prediction(model_digest="model-one"):
    return {
        "PK": "EVENT#soccer_test#event-id",
        "SK": f"PRED#T10#REV#4#TARGET#result_1x2#MODEL#{model_digest}",
        "entity_type": "SOCCER_MODEL_PREDICTION",
        "event_key": "EVENT#soccer_test#event-id",
        "event_id": "event-id",
        "sport_key": "soccer_test",
        "commence_time": "2026-08-14T14:00:00Z",
        "schedule_revision": 4,
        "schedule_identity": "schedule-identity",
        "horizon": "T10",
        "target": "result_1x2",
        "lock_at": "2026-08-14T13:49:00Z",
        "decision_target_at": "2026-08-14T13:50:00Z",
        "capture_opens_at": "2026-08-14T13:48:20Z",
        "lock_commit_deadline": "2026-08-14T13:49:50Z",
        "source_observed_at_max": "2026-08-14T13:48:55Z",
        "lock_version": "soccer-auto-t10-lock-v1",
        "feature_hash": "feature-hash",
        "feature_schema_version": "schema-v1",
        "coverage_certificate_version": "soccer-auto-coverage-certificate-v2",
        "coverage_certificate_digest": "c" * 64,
        "coverage_plan_digest": "plan-digest",
        "model_digest": model_digest,
        "model_authority": "CHAMPION",
        "prediction_status": "PUBLISHED",
        "selection": "HOME",
        "highest_probability_outcome": "HOME",
        "created_at": "2026-08-14T13:49:20Z",
        "publication_cutoff": "2026-08-14T13:50:00Z",
        "commit_deadline": "2026-08-14T13:49:50Z",
        "commit_headroom_seconds": 10,
        "autonomy_updated_at": "2026-08-14T13:49:00Z",
        "autonomy_updated_at_epoch_ms": 1_786_715_340_000,
        "event_metadata_revision": 12,
    }


class AtomicPublicPredictionTests(unittest.TestCase):
    def setUp(self):
        client = AtomicClient()
        self.store = SoccerStore.__new__(SoccerStore)
        self.store.ops = AtomicTable("soccer-ops", client)
        self.store.events = AtomicTable("soccer-events", client)
        self.store.predictions = AtomicTable("soccer-predictions", client)
        self.client = client
        self.store.ops.rows[("AUTONOMY", "STATE")] = {
            "PK": "AUTONOMY",
            "SK": "STATE",
            "authority": "AUTHORITATIVE",
            "automatic_prediction_allowed": True,
            "promotion_blocked": False,
            "updated_at": "2026-08-14T13:49:00Z",
            "updated_at_epoch_ms": 1_786_715_340_000,
        }
        self.store.events.rows[("EVENT#soccer_test#event-id", "METADATA")] = {
            "PK": "EVENT#soccer_test#event-id",
            "SK": "METADATA",
            "entity_type": "SOCCER_EVENT",
            "metadata_revision": 12,
            "schedule_revision": 4,
            "schedule_identity": "schedule-identity",
            "commence_time": "2026-08-14T14:00:00Z",
            "completed": False,
        }

    def test_public_binding_and_prediction_are_atomic_and_retry_idempotently(self):
        first = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )
        retry = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )

        self.assertEqual(first[0:2], (True, "PUBLIC_PREDICTION_WRITTEN"))
        self.assertEqual(
            retry[0:2],
            (False, "PUBLIC_PREDICTION_ALREADY_WRITTEN"),
        )
        self.assertEqual(
            sum(
                pk.startswith("PUBLIC_PREDICTION_BINDING#")
                for pk, _ in self.store.ops.rows
            ),
            1,
        )
        self.assertEqual(len(self.store.predictions.rows), 1)

    def test_matching_legacy_orphan_binding_is_recovered_transactionally(self):
        legacy_binding = binding()
        key = (legacy_binding["PK"], legacy_binding["SK"])
        self.store.ops.rows[key] = legacy_binding

        recovered = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )

        self.assertEqual(
            recovered[0:2],
            (True, "PUBLIC_PREDICTION_RECOVERED"),
        )
        self.assertEqual(
            sum(
                pk.startswith("PUBLIC_PREDICTION_BINDING#")
                for pk, _ in self.store.ops.rows
            ),
            1,
        )
        self.assertEqual(len(self.store.predictions.rows), 1)

    def test_changed_autonomy_or_event_authority_blocks_atomic_publication(self):
        self.store.ops.rows[("AUTONOMY", "STATE")]["promotion_blocked"] = True
        autonomy_result = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )
        self.assertEqual(autonomy_result[0:2], (False, "AUTONOMY_STATE_CHANGED"))
        self.assertEqual(len(self.store.predictions.rows), 0)
        self.assertFalse(
            any(
                pk.startswith("PUBLIC_PREDICTION_BINDING#")
                for pk, _ in self.store.ops.rows
            )
        )

        self.store.ops.rows[("AUTONOMY", "STATE")]["promotion_blocked"] = False
        self.store.events.rows[("EVENT#soccer_test#event-id", "METADATA")][
            "metadata_revision"
        ] = 13
        event_result = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )
        self.assertEqual(
            event_result[0:2],
            (False, "EVENT_SCHEDULE_AUTHORITY_CHANGED"),
        )
        self.assertEqual(len(self.store.predictions.rows), 0)
        self.assertIn("ConditionCheck", self.client.calls[-1]["TransactItems"][0])

    def test_different_existing_binding_cannot_gain_a_prediction(self):
        existing = binding("other-model")
        self.store.ops.rows[(existing["PK"], existing["SK"])] = existing

        blocked = self.store.put_public_prediction(
            binding=binding(),
            prediction=prediction(),
        )

        self.assertEqual(blocked[0:2], (False, "PUBLIC_MODEL_BINDING_MISMATCH"))
        self.assertEqual(self.store.predictions.rows, {})


if __name__ == "__main__":
    unittest.main()
