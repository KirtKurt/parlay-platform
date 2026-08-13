from __future__ import annotations

import unittest

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.storage import SoccerStore, plain  # noqa: E402


class EventTable:
    def __init__(self, current):
        self.current = current
        self.written = None

    def get_item(self, **kwargs):
        return {"Item": self.current} if self.current else {}

    def put_item(self, **kwargs):
        self.written = kwargs["Item"]


class OpsTable:
    def __init__(self, current):
        self.current = current
        self.updated = None

    def get_item(self, **kwargs):
        return {"Item": self.current} if self.current else {}

    def update_item(self, **kwargs):
        self.updated = kwargs


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
    def test_kickoff_revision_invalidates_prior_dispatch_state(self) -> None:
        table = EventTable(
            {
                "PK": "SOCCER_EVENT#digest",
                "SK": "METADATA",
                "event_key": "SOCCER_EVENT#digest",
                "event_id": "event-id",
                "sport_key": "soccer_future_league",
                "commence_time": "2026-08-14T14:00:00Z",
                "schedule_revision": 3,
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
