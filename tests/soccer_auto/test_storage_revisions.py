from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.canonical import digest, schedule_identity  # noqa: E402
from soccer_auto.storage import SoccerStore, plain  # noqa: E402


class EventTable:
    def __init__(self, current):
        self.current = current
        self.written = None

    def get_item(self, **kwargs):
        return {"Item": self.current} if self.current else {}

    def put_item(self, **kwargs):
        self.written = kwargs["Item"]


class RacingEventTable(EventTable):
    def __init__(self, current, concurrent_mutation):
        super().__init__(current)
        self.concurrent_mutation = concurrent_mutation
        self.put_attempts = 0

    def put_item(self, **kwargs):
        self.put_attempts += 1
        if self.put_attempts == 1:
            self._assert_catalog_revision_condition(kwargs)
            self.current = {
                **self.current,
                **self.concurrent_mutation,
                "metadata_revision": int(self.current["metadata_revision"]) + 1,
            }
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}},
                "PutItem",
            )
        self.written = kwargs["Item"]
        self.current = kwargs["Item"]

    def _assert_catalog_revision_condition(self, kwargs):
        if "metadata_revision=:metadata_revision" not in kwargs["ConditionExpression"]:
            raise AssertionError("catalog write must CAS the shared metadata revision")
        if kwargs["ExpressionAttributeValues"].get(":metadata_revision") != int(
            self.current["metadata_revision"]
        ):
            raise AssertionError("catalog CAS used the wrong metadata revision")


class RacingSlotsTable:
    def __init__(self):
        self.initial = {
            "PK": "EVENT#soccer_future_league#event-id",
            "SK": "SLOT#2026-08-14T13:00:00Z#REV#4#SCOPE#placeholder",
            "attempt_id": "initial-attempt",
            "observed_at": "2026-08-14T13:00:01Z",
            "commence_time": "2026-08-14T14:00:00Z",
            "raw_uri": "s3://raw/initial.json",
            "payload_sha256": "same-payload",
            "bookmaker_count": 1,
            "market_count": 1,
            "valid": True,
        }
        self.current = dict(self.initial)
        self.pointer_puts = 0

    def get_item(self, **kwargs):
        return {"Item": dict(self.current)}

    def put_item(self, **kwargs):
        if str(kwargs["Item"]["SK"]).startswith("ATTEMPT#"):
            return
        self.pointer_puts += 1
        if self.pointer_puts == 1:
            # Another worker promotes the same raw payload at a later response
            # time. A payload-hash CAS cannot detect this ABA transition.
            self.current = {
                **self.initial,
                "attempt_id": "newer-same-payload-attempt",
                "observed_at": "2026-08-14T13:00:50Z",
            }
            expected = kwargs["ExpressionAttributeValues"][":expected"]
            if expected != self.current["attempt_id"]:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )
        self.current = kwargs["Item"]


class OpsTable:
    def __init__(self, current):
        self.current = current
        self.updated = None

    def get_item(self, **kwargs):
        return {"Item": self.current} if self.current else {}

    def update_item(self, **kwargs):
        self.updated = kwargs


class EventUpdateTable:
    def __init__(self):
        self.calls = []

    def update_item(self, **kwargs):
        self.calls.append(kwargs)


class PaginatedSlotsTable:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if "ExclusiveStartKey" not in kwargs:
            return {
                "Items": [
                    {
                        "SK": "SLOT#2026-08-14T12:00:00Z#REV#4#SCOPE#a",
                        "schedule_revision": 4,
                        "slot_start": "2026-08-14T12:00:00Z",
                        "observed_at": "2026-08-14T12:00:10Z",
                        "slot_seconds": 60,
                        "grace_seconds": 20,
                        "scope_hash": "a",
                    }
                ],
                "LastEvaluatedKey": {"PK": "event", "SK": "page-1"},
            }
        return {
            "Items": [
                {
                    "SK": "SLOT#2026-08-14T13:00:00Z#REV#4#SCOPE#b",
                    "schedule_revision": 4,
                    "slot_start": "2026-08-14T13:00:00Z",
                    "observed_at": "2026-08-14T13:00:05Z",
                    "slot_seconds": 60,
                    "grace_seconds": 20,
                    "scope_hash": "b",
                }
            ]
        }


class EventRevisionTests(unittest.TestCase):
    def test_event_mutations_advance_the_catalog_cas_revision(self) -> None:
        table = EventUpdateTable()
        store = SoccerStore.__new__(SoccerStore)
        store.events = table
        store.mark_dispatched(
            "EVENT#soccer_future_league#event-id",
            "2026-08-14T04:30:00Z",
        )
        store.mark_completed(
            "EVENT#soccer_future_league#event-id",
            "2026-08-14T16:30:00Z",
        )
        self.assertEqual(len(table.calls), 2)
        for call in table.calls:
            self.assertIn("ADD metadata_revision :one", call["UpdateExpression"])
            self.assertEqual(call["ExpressionAttributeValues"][":one"], 1)

    def test_catalog_cas_preserves_concurrent_completion_and_dispatch_updates(self) -> None:
        base = {
            "PK": "EVENT#soccer_future_league#event-id",
            "SK": "METADATA",
            "event_key": "EVENT#soccer_future_league#event-id",
            "event_id": "event-id",
            "sport_key": "soccer_future_league",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 3,
            "metadata_revision": 7,
            "last_seen_at": "2026-08-14T04:00:00Z",
            "first_seen_at": "2026-08-01T00:00:00Z",
            "completed": False,
            "GSI1PK": "ACTIVE",
        }
        mutations = (
            {
                "completed": True,
                "completed_seen_at": "2026-08-14T04:30:00Z",
                "GSI1PK": "COMPLETED",
            },
            {"last_dispatched_at": "2026-08-14T04:30:00Z"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                table = RacingEventTable(dict(base), mutation)
                store = SoccerStore.__new__(SoccerStore)
                store.events = table
                result = store.put_event(
                    {
                        "id": "event-id",
                        "sport_key": "soccer_future_league",
                        "commence_time": "2026-08-14T14:00:00Z",
                        "home_team": "Home",
                        "away_team": "Away",
                    },
                    "2026-08-14T05:00:00Z",
                )
                self.assertEqual(table.put_attempts, 2)
                self.assertEqual(result["metadata_revision"], 9)
                for key, value in mutation.items():
                    self.assertEqual(result.get(key), value)

    def test_canonical_pointer_attempt_cas_rejects_same_payload_aba(self) -> None:
        event = {
            "event_key": "EVENT#soccer_future_league#event-id",
            "event_id": "event-id",
            "sport_key": "soccer_future_league",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 4,
        }
        event["schedule_identity"] = schedule_identity(event)
        payload = {
            "id": "event-id",
            "sport_key": "soccer_future_league",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "bookmakers": [
                {
                    "key": "book",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [{"name": "Home", "price": 2.0}],
                        }
                    ],
                }
            ],
        }
        table = RacingSlotsTable()
        store = SoccerStore.__new__(SoccerStore)
        store.slots = table
        store.archive_json = lambda *args, **kwargs: (
            "s3://raw/candidate.json",
            digest(payload),
        )
        result = store.put_snapshot_attempt(
            event=event,
            payload=payload,
            observed_at="2026-08-14T13:00:20Z",
            bookmakers=["book"],
            markets=["h2h"],
            request_metadata={},
        )
        self.assertFalse(result["canonical_promoted"])
        self.assertEqual(table.pointer_puts, 1)
        self.assertEqual(table.current["attempt_id"], "newer-same-payload-attempt")
        self.assertEqual(table.current["observed_at"], "2026-08-14T13:00:50Z")

    def test_kickoff_revision_invalidates_prior_dispatch_state(self) -> None:
        table = EventTable(
            {
                "PK": "SOCCER_EVENT#digest",
                "SK": "METADATA",
                "event_key": "SOCCER_EVENT#digest",
                "event_id": "event-id",
                "sport_key": "soccer_future_league",
                "commence_time": "2026-08-14T14:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
                "schedule_revision": 3,
                "last_seen_at": "2026-08-14T04:00:00Z",
                "last_dispatched_at": "2026-08-14T04:00:00Z",
                "first_seen_at": "2026-08-01T00:00:00Z",
                "completed": False,
            }
        )
        store = SoccerStore.__new__(SoccerStore)
        store.events = table
        result = store.put_event(
            {
                "id": "event-id",
                "sport_key": "soccer_future_league",
                "commence_time": "2026-08-15T16:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
            },
            "2026-08-14T05:00:00Z",
        )
        written = plain(table.written)
        self.assertEqual(result["schedule_revision"], 4)
        self.assertIsNone(written["last_dispatched_at"])
        self.assertEqual(written["commence_time"], "2026-08-15T16:00:00Z")

    def test_out_of_order_event_observation_cannot_repaint_newer_schedule(self) -> None:
        current = {
            "PK": "EVENT#soccer_future_league#event-id",
            "SK": "METADATA",
            "event_key": "EVENT#soccer_future_league#event-id",
            "event_id": "event-id",
            "sport_key": "soccer_future_league",
            "commence_time": "2026-08-15T16:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": 4,
            "last_seen_at": "2026-08-14T06:00:00Z",
            "completed": False,
        }
        table = EventTable(current)
        store = SoccerStore.__new__(SoccerStore)
        store.events = table
        result = store.put_event(
            {
                "id": "event-id",
                "sport_key": "soccer_future_league",
                "commence_time": "2026-08-14T14:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
            },
            "2026-08-14T05:00:00Z",
        )
        self.assertEqual(result["commence_time"], current["commence_time"])
        self.assertEqual(result["schedule_revision"], 4)
        self.assertIsNone(table.written)

    def test_team_change_at_same_kickoff_creates_new_schedule_revision(self) -> None:
        current = {
            "PK": "EVENT#soccer_future_league#event-id",
            "SK": "METADATA",
            "event_key": "EVENT#soccer_future_league#event-id",
            "event_id": "event-id",
            "sport_key": "soccer_future_league",
            "commence_time": "2026-08-15T16:00:00Z",
            "home_team": "Old Home",
            "away_team": "Away",
            "schedule_revision": 4,
            "last_seen_at": "2026-08-14T05:00:00Z",
            "completed": False,
        }
        table = EventTable(current)
        store = SoccerStore.__new__(SoccerStore)
        store.events = table
        result = store.put_event(
            {
                "id": "event-id",
                "sport_key": "soccer_future_league",
                "commence_time": "2026-08-15T16:00:00Z",
                "home_team": "New Home",
                "away_team": "Away",
            },
            "2026-08-14T06:00:00Z",
        )
        self.assertEqual(result["schedule_revision"], 5)
        self.assertIsNone(result["last_dispatched_at"])

    def test_earlier_fixture_revision_recalculates_actual_opening_drift(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.ops = OpsTable(
            {
                "PK": "COLLECTION_WINDOW",
                "SK": "2026-08-14",
                "actual_first_provider_call_at": "2026-08-14T10:00:00Z",
                "scheduled_open_at": "2026-08-14T10:00:00Z",
                "first_event_key": "event",
            }
        )
        store.record_collection_window_call(
            {"event_key": "event"},
            {
                "match_day": "2026-08-14",
                "timezone": "America/New_York",
                "first_kickoff": "2026-08-14T14:00:00Z",
                "opens_at": "2026-08-14T04:00:00Z",
                "event_count": 20,
            },
            "2026-08-14T10:01:00Z",
        )
        values = plain(store.ops.updated["ExpressionAttributeValues"])
        self.assertEqual(values[":drift"], 6 * 60 * 60 * 1000)
        self.assertEqual(values[":sla"], "LATE_DISCOVERY_OR_SCHEDULER_DRIFT")
        self.assertTrue(values[":revised"])

    def test_canonical_slot_read_consumes_every_dynamodb_page(self) -> None:
        store = SoccerStore.__new__(SoccerStore)
        store.slots = PaginatedSlotsTable()
        rows = store.canonical_slots_before(
            "event",
            "2026-08-14T13:15:00Z",
            schedule_revision=4,
        )
        self.assertEqual([row["scope_hash"] for row in rows], ["a", "b"])
        self.assertEqual(len(store.slots.calls), 2)
        self.assertEqual(
            store.slots.calls[1]["ExclusiveStartKey"],
            {"PK": "event", "SK": "page-1"},
        )


if __name__ == "__main__":
    unittest.main()
