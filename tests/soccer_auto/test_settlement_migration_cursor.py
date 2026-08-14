from __future__ import annotations

import unittest

from botocore.exceptions import ClientError

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import digest  # noqa: E402
from soccer_auto.storage import SoccerStore  # noqa: E402


class Ops:
    def __init__(self, item=None, *, fail_condition=False):
        self.item = item or {}
        self.fail_condition = fail_condition
        self.put = None

    def get_item(self, **kwargs):
        self.get = kwargs
        return {"Item": self.item} if self.item else {}

    def put_item(self, **kwargs):
        self.put = kwargs
        if self.fail_condition:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}},
                "PutItem",
            )
        return {}


class Settlements:
    def __init__(self, response):
        self.response = response
        self.scan_kwargs = None

    def scan(self, **kwargs):
        self.scan_kwargs = kwargs
        return self.response


class SettlementMigrationCursorTests(unittest.TestCase):
    def store(self, *, ops, settlements):
        store = object.__new__(SoccerStore)
        store.ops = ops
        store.settlements = settlements
        return store

    def test_page_is_bounded_and_resumes_from_signed_cursor(self) -> None:
        cursor = {"PK": "event-1", "SK": "FINAL#v1"}
        next_cursor = {"PK": "event-2", "SK": "FINAL#v1"}
        ops = Ops(
            {
                "PK": "MIGRATION_STATE",
                "SK": "SETTLEMENT_ADMISSIBILITY_V1",
                "next_start_key": cursor,
                "cursor_digest": digest(cursor),
                "cycle": 2,
                "page_index": 3,
            }
        )
        settlements = Settlements(
            {"Items": [{"PK": "event-2"}], "LastEvaluatedKey": next_cursor}
        )
        page = self.store(ops=ops, settlements=settlements).settlement_admissibility_migration_page(
            limit=17
        )
        self.assertEqual(settlements.scan_kwargs["Limit"], 17)
        self.assertTrue(settlements.scan_kwargs["ConsistentRead"])
        self.assertEqual(settlements.scan_kwargs["ExclusiveStartKey"], cursor)
        self.assertEqual(page["rows"], [{"PK": "event-2"}])
        self.assertEqual(page["next_start_key"], next_cursor)
        self.assertEqual(page["cursor_digest"], digest(cursor))
        self.assertEqual(page["cycle"], 2)
        self.assertEqual(page["page_index"], 3)

    def test_tampered_migration_cursor_fails_closed(self) -> None:
        cursor = {"PK": "event-1", "SK": "FINAL#v1"}
        store = self.store(
            ops=Ops(
                {
                    "next_start_key": cursor,
                    "cursor_digest": "tampered",
                }
            ),
            settlements=Settlements({"Items": []}),
        )
        with self.assertRaisesRegex(ValueError, "cursor is invalid"):
            store.settlement_admissibility_migration_page(limit=10)

    def test_checkpoint_advances_page_with_compare_and_swap(self) -> None:
        next_cursor = {"PK": "event-2", "SK": "FINAL#v1"}
        ops = Ops()
        store = self.store(ops=ops, settlements=Settlements({"Items": []}))
        updated = store.checkpoint_settlement_admissibility_migration(
            expected_cursor_digest=digest({}),
            next_start_key=next_cursor,
            cycle=4,
            page_index=6,
            observed_at="2026-08-14T12:00:00Z",
        )
        self.assertTrue(updated)
        self.assertEqual(
            ops.put["ConditionExpression"],
            "attribute_not_exists(SK) OR cursor_digest=:expected",
        )
        self.assertEqual(
            ops.put["ExpressionAttributeValues"][":expected"], digest({})
        )
        item = ops.put["Item"]
        self.assertEqual(item["next_start_key"], next_cursor)
        self.assertEqual(item["cursor_digest"], digest(next_cursor))
        self.assertEqual(item["cycle"], 4)
        self.assertEqual(item["page_index"], 7)

    def test_checkpoint_condition_failure_is_retryable(self) -> None:
        ops = Ops(fail_condition=True)
        store = self.store(ops=ops, settlements=Settlements({"Items": []}))
        updated = store.checkpoint_settlement_admissibility_migration(
            expected_cursor_digest=digest({}),
            next_start_key={},
            cycle=4,
            page_index=6,
            observed_at="2026-08-14T12:00:00Z",
        )
        self.assertFalse(updated)


if __name__ == "__main__":
    unittest.main()
