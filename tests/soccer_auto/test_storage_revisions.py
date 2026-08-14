from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.canonical import digest, schedule_identity  # noqa: E402
import soccer_auto.storage as storage_module  # noqa: E402
from soccer_auto.storage import (  # noqa: E402
    COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES,
    COVERAGE_PLAN_VERSION,
    EVENT_INVENTORY_AUTHORITY_VERSION,
    SoccerStore,
    plain,
)


class EventTable:
    def __init__(self, current):
        self.current = current
        self.written = None

    def get_item(self, **kwargs):
        return {"Item": self.current} if self.current else {}

    def put_item(self, **kwargs):
        self.written = kwargs["Item"]


class AuthoritativeEventScan:
    """A stale GSI projection beside the authoritative paginated base rows."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.scan_calls = []
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(dict(kwargs))
        return {"Items": []}

    def scan(self, **kwargs):
        self.scan_calls.append(dict(kwargs))
        index = 1 if kwargs.get("ExclusiveStartKey") else 0
        return dict(self.pages[index])


class PaginatedRegistry:
    def __init__(self):
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(dict(kwargs))
        if kwargs.get("ExclusiveStartKey"):
            return {
                "Items": [
                    {"PK": "COMPETITION", "SK": "soccer_two", "active": False}
                ]
            }
        return {
            "Items": [
                {"PK": "COMPETITION", "SK": "soccer_one", "active": True}
            ],
            "LastEvaluatedKey": {"PK": "COMPETITION", "SK": "soccer_one"},
        }


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
            schedule_revision=1,
            schedule_identity_value="identity",
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
            regions=[],
            markets=["h2h"],
            coverage_plan_observed_at="2026-08-14T12:59:00Z",
            coverage_plan_digest="plan-digest",
            coverage_batch_digest="batch-digest",
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


def conditional_failure(operation: str = "PutItem") -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, operation
    )


class CoverageLeaseOps:
    """Small DDB fake for exact-summary CASes and execution-token fencing."""

    def __init__(self) -> None:
        self.rows = {}

    @staticmethod
    def _key(value):
        return str(value["PK"]), str(value["SK"])

    def get_item(self, *, Key, **kwargs):
        row = self.rows.get(self._key(Key))
        return {"Item": dict(row)} if row else {}

    def put_item(self, *, Item, ConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        key = self._key(Item)
        current = self.rows.get(key)
        values = ExpressionAttributeValues or {}
        if ConditionExpression and "lease_expires_at <= :now" in ConditionExpression:
            if current and not (
                current.get("execution_state") != values[":completed"]
                and int(current.get("lease_expires_at") or 0) <= int(values[":now"])
            ):
                raise conditional_failure()
        elif ConditionExpression and "observed_at < :observed_at" in ConditionExpression:
            if current and str(current.get("observed_at") or "") >= str(values[":observed_at"]):
                raise conditional_failure()
        elif ConditionExpression and "plan_observed_at=:plan_at" in ConditionExpression:
            if (
                not current
                or current.get("plan_observed_at") != values[":plan_at"]
                or current.get("plan_digest") != values[":plan_digest"]
                or int(current.get("summary_revision") or 0) != int(values[":revision"])
            ):
                raise conditional_failure()
        elif ConditionExpression and "summary_revision=:revision" in ConditionExpression:
            if current and int(current.get("summary_revision") or 0) != int(values[":revision"]):
                raise conditional_failure()
        self.rows[key] = dict(Item)

    def update_item(self, *, Key, ConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        key = self._key(Key)
        current = self.rows.get(key)
        values = ExpressionAttributeValues or {}
        if (
            not current
            or current.get("execution_state") != values.get(":running")
            or current.get("execution_token") != values.get(":token")
        ):
            raise conditional_failure("UpdateItem")
        current.update(
            {
                "execution_state": values[":completed"],
                "completed_at": values[":completed_at"],
                "expires_at": values[":expires"],
            }
        )
        current.pop("lease_expires_at", None)

    def delete_item(self, *, Key, ConditionExpression=None, ExpressionAttributeValues=None, **kwargs):
        key = self._key(Key)
        current = self.rows.get(key)
        values = ExpressionAttributeValues or {}
        if (
            not current
            or current.get("execution_state") != values.get(":running")
            or current.get("execution_token") != values.get(":token")
        ):
            raise conditional_failure("DeleteItem")
        del self.rows[key]


class CoverageStorageRegressionTests(unittest.TestCase):
    event_key = "EVENT#soccer_test#event-id"
    generation = "2026-08-14T04:00:00Z"

    def setUp(self) -> None:
        self.store = SoccerStore.__new__(SoccerStore)
        self.store.ops = CoverageLeaseOps()

    def _event(self, revision: int = 1):
        event = {
            "event_key": self.event_key,
            "event_id": "event-id",
            "sport_key": "soccer_test",
            "commence_time": "2026-08-14T14:00:00Z",
            "home_team": "Home",
            "away_team": "Away",
            "schedule_revision": revision,
        }
        event["schedule_identity"] = schedule_identity(event)
        return event

    def _latest(self, **values):
        row = {
            "PK": "COVERAGE_LATEST",
            "SK": self.event_key,
            "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
            "event_key": self.event_key,
            "summary_revision": 1,
            "discovery_observed_at": self.generation,
            "discovery_status": "HTTP_200",
            "schedule_revision": 1,
            "schedule_identity": self._event()["schedule_identity"],
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": self.generation,
            "plan_digest": "plan",
            "terminal_fetch_batch_digests": [],
            **values,
        }
        self.store.ops.rows[(row["PK"], row["SK"])] = row
        return row

    def test_same_generation_v2_summary_migrates_to_v3_before_terminal_discovery(self) -> None:
        event = self._event()
        self._latest(plan_version="soccer-auto-coverage-plan-v2")
        migrated = self.store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=self.generation,
            status="QUEUED",
            observed_at="2026-08-14T04:00:01Z",
        )
        self.assertTrue(migrated["latest_summary_updated"])
        self.assertEqual(migrated["schedule_revision"], 1)
        self.assertFalse(migrated.get("plan_observed_at"))
        plan = self.store.put_coverage_plan(
            self.event_key,
            {"book": {"markets": ["h2h"]}},
            "2026-08-14T04:00:02Z",
            event=event,
            discovery_observed_at=self.generation,
            request_markets=("h2h",),
        )
        self.assertTrue(plan["latest_summary_updated"])
        self.assertEqual(plan["plan_version"], COVERAGE_PLAN_VERSION)

    def test_v2_latest_summary_cannot_authorize_a_fetch(self) -> None:
        self._latest(plan_version="soccer-auto-coverage-plan-v2")
        self.assertFalse(
            self.store.coverage_plan_is_current(
                self.event_key,
                plan_observed_at=self.generation,
                plan_digest="plan",
            )
        )

    def test_same_generation_queued_dispatch_can_be_resent_after_a_hard_gap(self) -> None:
        event = self._event()
        first = self.store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=self.generation,
            status="QUEUED",
            observed_at="2026-08-14T04:00:01Z",
        )
        resent = self.store.put_coverage_discovery_attempt(
            event,
            discovery_observed_at=self.generation,
            status="QUEUED",
            observed_at="2026-08-14T04:01:01Z",
        )
        self.assertTrue(first["latest_summary_updated"])
        self.assertTrue(resent["latest_summary_updated"])
        self.assertEqual(
            resent["discovery_status_observed_at"],
            "2026-08-14T04:01:01Z",
        )

    def test_active_event_authority_uses_strong_base_scan_not_stale_gsi(self) -> None:
        active = self._event()
        active.update(
            {
                "PK": self.event_key,
                "SK": "METADATA",
                "entity_type": "SOCCER_EVENT",
                "schedule_revision": 3,
            }
        )
        completed = {
            **active,
            "PK": "EVENT#completed",
            "event_key": "EVENT#completed",
            "completed": True,
        }
        outside = {
            **active,
            "PK": "EVENT#outside",
            "event_key": "EVENT#outside",
            "commence_time": "2026-10-01T14:00:00Z",
        }
        table = AuthoritativeEventScan(
            [
                {"Items": [completed, outside], "LastEvaluatedKey": {"PK": "next"}},
                {"Items": [active]},
            ]
        )
        store = SoccerStore.__new__(SoccerStore)
        store.events = table
        rows = store.active_events_between(
            "2026-08-14T00:00:00Z",
            "2026-08-15T00:00:00Z",
        )
        self.assertEqual([row["event_key"] for row in rows], [self.event_key])
        self.assertEqual(rows[0]["schedule_revision"], 3)
        self.assertFalse(table.query_calls)
        self.assertEqual(len(table.scan_calls), 2)
        self.assertTrue(all(call.get("ConsistentRead") is True for call in table.scan_calls))

    def test_inventory_competition_scope_is_strong_and_paginated(self) -> None:
        registry = PaginatedRegistry()
        store = SoccerStore.__new__(SoccerStore)
        store.registry = registry
        rows = store.list_competitions()
        self.assertEqual([row["SK"] for row in rows], ["soccer_one", "soccer_two"])
        self.assertEqual(len(registry.calls), 2)
        self.assertTrue(
            all(call.get("ConsistentRead") is True for call in registry.calls)
        )
        self.assertEqual(
            [row["SK"] for row in store.list_competitions(active_only=True)],
            ["soccer_one"],
        )

    def test_inventory_generation_lease_recovers_and_fences_the_scan(self) -> None:
        first = self.store.begin_event_inventory_generation(
            generation_id="first",
            observed_at="2026-08-14T04:00:00Z",
            lease_seconds=60,
        )
        busy = self.store.begin_event_inventory_generation(
            generation_id="busy",
            observed_at="2026-08-14T04:00:30Z",
            lease_seconds=60,
        )
        reclaimed = self.store.begin_event_inventory_generation(
            generation_id="reclaimed",
            observed_at="2026-08-14T04:01:00Z",
            lease_seconds=60,
        )
        self.assertTrue(first["acquired"])
        self.assertFalse(busy["acquired"])
        self.assertTrue(reclaimed["acquired"])
        finished = self.store.finish_event_inventory_generation(
            generation_id="reclaimed",
            observed_at="2026-08-14T04:01:01Z",
            success=True,
            competitions_refreshed=1,
        )
        self.assertTrue(finished["updated"])

        active = {
            **self._event(),
            "PK": self.event_key,
            "SK": "METADATA",
            "entity_type": "SOCCER_EVENT",
        }
        self.store.events = AuthoritativeEventScan([{"Items": [active]}])
        rows, proof = self.store.authoritative_active_events_between(
            "2026-08-14T00:00:00Z",
            "2026-08-15T00:00:00Z",
            observed_at="2026-08-14T04:02:00Z",
        )
        self.assertEqual([row["event_key"] for row in rows], [self.event_key])
        self.assertTrue(proof["valid"])
        self.assertEqual(proof["generation_id"], "reclaimed")

        self.store.begin_event_inventory_generation(
            generation_id="next",
            observed_at="2026-08-14T04:03:00Z",
        )
        _, running = self.store.authoritative_active_events_between(
            "2026-08-14T00:00:00Z",
            "2026-08-15T00:00:00Z",
            observed_at="2026-08-14T04:03:01Z",
        )
        self.assertFalse(running["valid"])
        self.assertEqual(running["reason"], "INVENTORY_GENERATION_NOT_COMPLETED")

    def test_inventory_failure_evidence_is_bounded_before_persistence(self) -> None:
        self.store.begin_event_inventory_generation(
            generation_id="failed-generation",
            observed_at="2026-08-14T04:00:00Z",
        )
        finished = self.store.finish_event_inventory_generation(
            generation_id="failed-generation",
            observed_at="2026-08-14T04:00:01Z",
            success=False,
            competitions_refreshed=0,
            failures=[
                {
                    "sport_key": "soccer_test",
                    "error": "x" * 5000,
                }
            ],
        )
        self.assertTrue(finished["updated"])
        self.assertEqual(finished["authority_state"], "FAILED")
        self.assertEqual(len(finished["failure_sample"][0]["error"]), 1000)

    def test_discovery_lease_retries_v2_but_completes_only_after_v3_terminal_summary(self) -> None:
        self._latest(plan_version="soccer-auto-coverage-plan-v2")
        first = self.store.begin_coverage_discovery_execution(
            event_key=self.event_key,
            discovery_observed_at=self.generation,
            schedule_revision=1,
            execution_token="v2-worker",
            observed_at="2026-08-14T04:00:00Z",
        )
        self.assertTrue(first["acquired"])
        self.assertFalse(
            self.store.complete_coverage_discovery_execution(
                event_key=self.event_key,
                discovery_observed_at=self.generation,
                schedule_revision=1,
                execution_token="v2-worker",
                observed_at="2026-08-14T04:00:01Z",
            )
        )
        self._latest(plan_version=COVERAGE_PLAN_VERSION)
        self.assertTrue(
            self.store.complete_coverage_discovery_execution(
                event_key=self.event_key,
                discovery_observed_at=self.generation,
                schedule_revision=1,
                execution_token="v2-worker",
                observed_at="2026-08-14T04:00:02Z",
            )
        )
        terminal = self.store.begin_coverage_discovery_execution(
            event_key=self.event_key,
            discovery_observed_at=self.generation,
            schedule_revision=1,
            execution_token="new-worker",
            observed_at="2026-08-14T04:00:03Z",
        )
        self.assertEqual(terminal, {"acquired": False, "state": "COMPLETED"})

    def test_fetch_lease_acquire_busy_reclaim_release_and_completion_are_token_fenced(self) -> None:
        batch = "batch"
        common = {
            "event_key": self.event_key,
            "plan_digest": "plan",
            "batch_digest": batch,
        }
        self.assertTrue(self.store.begin_coverage_fetch_execution(
            **common, execution_token="first", observed_at="2026-08-14T04:00:00Z", lease_seconds=30
        )["acquired"])
        self.assertFalse(self.store.begin_coverage_fetch_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:01Z", lease_seconds=30
        )["acquired"])
        self.assertTrue(self.store.begin_coverage_fetch_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:30Z", lease_seconds=30
        )["acquired"])
        self.store.release_coverage_fetch_execution(**common, execution_token="first")
        execution = next(row for row in self.store.ops.rows.values() if row.get("batch_digest") == batch)
        self.assertEqual(execution["execution_token"], "second")
        self._latest(terminal_fetch_batch_digests=[batch])
        self.assertFalse(self.store.complete_coverage_fetch_execution(
            **common, execution_token="first", observed_at="2026-08-14T04:00:31Z"
        ))
        self.assertTrue(self.store.complete_coverage_fetch_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:31Z"
        ))
        self.assertEqual(
            self.store.begin_coverage_fetch_execution(
                **common, execution_token="third", observed_at="2026-08-14T04:00:32Z"
            ),
            {"acquired": False, "state": "COMPLETED"},
        )

    def test_discovery_lease_acquire_busy_reclaim_release_and_completion_are_token_fenced(self) -> None:
        common = {
            "event_key": self.event_key,
            "discovery_observed_at": self.generation,
            "schedule_revision": 1,
        }
        self.assertTrue(self.store.begin_coverage_discovery_execution(
            **common, execution_token="first", observed_at="2026-08-14T04:00:00Z", lease_seconds=30
        )["acquired"])
        self.assertFalse(self.store.begin_coverage_discovery_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:01Z", lease_seconds=30
        )["acquired"])
        self.assertTrue(self.store.begin_coverage_discovery_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:30Z", lease_seconds=30
        )["acquired"])
        self.store.release_coverage_discovery_execution(**common, execution_token="first")
        execution = next(
            row for row in self.store.ops.rows.values()
            if row.get("entity_type") == "SOCCER_COVERAGE_DISCOVERY_EXECUTION"
        )
        self.assertEqual(execution["execution_token"], "second")
        self._latest()
        self.assertFalse(self.store.complete_coverage_discovery_execution(
            **common, execution_token="first", observed_at="2026-08-14T04:00:31Z"
        ))
        self.assertTrue(self.store.complete_coverage_discovery_execution(
            **common, execution_token="second", observed_at="2026-08-14T04:00:31Z"
        ))
        self.assertEqual(
            self.store.begin_coverage_discovery_execution(
                **common, execution_token="third", observed_at="2026-08-14T04:00:32Z"
            ),
            {"acquired": False, "state": "COMPLETED"},
        )

    def test_plan_and_manifest_size_guards_fail_closed_before_ddb(self) -> None:
        with patch.object(
            storage_module,
            "ddb_item_size_bytes",
            side_effect=[COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES + 1, 0],
        ):
            rejected = self.store.put_coverage_plan(
                self.event_key,
                {"book": {"markets": ["h2h"]}},
                self.generation,
                event=self._event(),
            )
        self.assertEqual(rejected["discovery_status"], "PLAN_SIZE_LIMIT")
        self.assertFalse(rejected["expected_pairs"])
        with patch.object(
            storage_module, "ddb_item_size_bytes", return_value=COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES + 1
        ):
            with self.assertRaisesRegex(RuntimeError, "manifest exceeds"):
                self.store.put_coverage_dispatch_manifest(
                    [{
                        "event_key": self.event_key,
                        "commence_time": "2026-08-14T14:00:00Z",
                        "schedule_revision": 1,
                        "schedule_identity": self._event()["schedule_identity"],
                    }],
                    observed_at=self.generation,
                    inventory_authority={
                        "valid": True,
                        "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
                        "generation_id": "inventory-test",
                        "completed_at": self.generation,
                        "authority_revision": 2,
                        "reason": "",
                    },
                )
        manifest = self.store.latest_coverage_dispatch_manifest()
        self.assertEqual(manifest["manifest_error"], "DDB_ITEM_SIZE_LIMIT")

    def test_fetch_and_summary_merge_size_guards_persist_small_fail_closed_evidence(self) -> None:
        plan = self.store.put_coverage_plan(
            self.event_key,
            {"book": {"markets": ["h2h"]}},
            self.generation,
            event=self._event(),
            request_markets=("h2h",),
        )
        fetch_kwargs = {
            "observed_at": "2026-08-14T04:00:01Z",
            "requested_bookmakers": (),
            "requested_markets": ("h2h",),
            "plan_observed_at": plan["plan_observed_at"],
            "plan_digest": plan["plan_digest"],
            "planned_pairs": ("book|h2h",),
            "raw_returned_pairs": ("book|h2h",),
            "batch_digest": "batch",
        }
        with patch.object(
            storage_module,
            "ddb_item_size_bytes",
            side_effect=[COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES + 1, 0],
        ):
            evidence_limited = self.store.put_coverage_fetch(
                self.event_key, {"bookmakers": []}, **fetch_kwargs
            )
        self.assertEqual(evidence_limited["outcome"], "EVIDENCE_SIZE_LIMIT")
        self.assertEqual(evidence_limited["coverage_error"], "DDB_ITEM_SIZE_LIMIT")
        with patch.object(
            storage_module,
            "ddb_item_size_bytes",
            side_effect=[0, COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES + 1],
        ):
            self.store.put_coverage_fetch(
                self.event_key, {"bookmakers": []}, **fetch_kwargs
            )
        summary = self.store.latest_coverage_summary(self.event_key)
        self.assertEqual(summary["discovery_status"], "SUMMARY_SIZE_LIMIT")
        self.assertEqual(summary["coverage_error"], "DDB_ITEM_SIZE_LIMIT")

    def test_nested_split_child_arriving_before_parent_frontier_is_not_acknowledged(self) -> None:
        plan = self.store.put_coverage_plan(
            self.event_key,
            {"book": {"markets": ["h2h"]}},
            self.generation,
            event=self._event(),
            request_markets=("h2h",),
        )
        root = storage_module.coverage_expected_batch_digests(
            plan_digest=plan["plan_digest"],
            request_markets=plan["request_markets"],
            expected_pairs=plan["expected_pairs"],
        )[0]
        self.store.put_coverage_fanout_expected(
            self.event_key,
            plan_observed_at=plan["plan_observed_at"],
            plan_digest=plan["plan_digest"],
            batch_digests=(root,),
            observed_at="2026-08-14T04:00:01Z",
        )
        common = {
            "observed_at": "2026-08-14T04:00:02Z",
            "requested_bookmakers": (),
            "requested_markets": ("h2h",),
            "plan_observed_at": plan["plan_observed_at"],
            "plan_digest": plan["plan_digest"],
            "planned_pairs": ("book|h2h",),
            "split_group_digest": root,
        }
        self.store.put_coverage_fetch(
            self.event_key, {"bookmakers": []}, outcome="SPLIT_PENDING",
            batch_digest=root, split_child_leaf_ids=("parent", "sibling"), **common
        )
        early_child = self.store.put_coverage_fetch(
            self.event_key, {"bookmakers": []}, outcome="HTTP_200",
            batch_digest="grandchild-batch", split_leaf_id="grandchild",
            split_expected_leaf_ids=("grandchild",), **common
        )
        self.assertFalse(early_child["latest_summary_updated"])
        self.store.put_coverage_fetch(
            self.event_key, {"bookmakers": []}, outcome="SPLIT_PENDING",
            batch_digest="parent-batch", split_leaf_id="parent",
            split_child_leaf_ids=("grandchild",), **common
        )
        self.store.put_coverage_fetch(
            self.event_key, {"bookmakers": []}, outcome="HTTP_200",
            batch_digest="grandchild-batch", split_leaf_id="grandchild",
            split_expected_leaf_ids=("grandchild",), **common
        )
        self.store.put_coverage_fetch(
            self.event_key, {"bookmakers": []}, outcome="HTTP_200",
            batch_digest="sibling-batch", split_leaf_id="sibling",
            split_expected_leaf_ids=("grandchild", "sibling"), **common
        )
        summary = self.store.latest_coverage_summary(self.event_key)
        self.assertEqual(
            summary["split_batch_groups"][root]["completed_leaf_ids"],
            ["grandchild", "sibling"],
        )
        self.assertIn(root, summary["fanout_succeeded_batch_digests"])


if __name__ == "__main__":
    unittest.main()
