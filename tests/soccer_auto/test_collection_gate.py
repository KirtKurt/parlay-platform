from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import stable_event_key  # noqa: E402
from soccer_auto.collector import (  # noqa: E402
    EventAlreadyStarted,
    _discover_event,
    _fetch_event,
    _market_keys_for_sport,
    dispatch_handler,
    worker_handler,
)
from soccer_auto.odds_api import ApiResponse, OddsApiError  # noqa: E402
from soccer_auto.config import ALL_BOOKMAKER_REGIONS, SOCCER_MARKET_SEEDS  # noqa: E402


class FakeStore:
    def __init__(self, event, persisted_window=None):
        self.event = event
        self.persisted_window = persisted_window
        self.calls = []
        self.jobs = []
        self.failures = []
        self.inventory_writes = []
        self.coverage_fetches = []
        self.snapshot_writes = []
        self.budget_admissions = []

    def get_event(self, event_key):
        return self.event

    def active_events_between(self, start, end):
        return [self.event]

    def get_collection_window(self, match_day):
        return self.persisted_window

    def record_collection_window_call(self, event, window, observed_at):
        self.calls.append((event, window, observed_at))

    def provider_budget_available(self, operation, observed_at, estimated_cost=1):
        self.budget_admissions.append((operation, estimated_cost))
        return True

    def record_quota(self, *args, **kwargs):
        pass

    def archive_json(self, *args, **kwargs):
        return "s3://soccer/raw.json", "hash"

    def cumulative_market_inventory(self, event_key, **kwargs):
        return {}

    def put_market_inventory(self, event_key, payload, observed_at):
        self.inventory_writes.append((event_key, payload, observed_at))

    def put_coverage_plan(self, event_key, payload, observed_at):
        pass

    def put_coverage_fetch(self, *args, **kwargs):
        self.coverage_fetches.append((args, kwargs))

    def put_snapshot_attempt(self, **kwargs):
        self.snapshot_writes.append(kwargs)
        return {"event_key": kwargs["event"]["event_key"], "canonical_promoted": True}

    def record_collection_failure(self, **payload):
        self.failures.append(payload)

    def enqueue(self, payload):
        self.jobs.append(payload)


class FakeClient:
    def __init__(self):
        self.market_calls = 0

    def event_markets(self, sport_key, event_id, *, regions):
        self.market_calls += 1
        return ApiResponse(data={"bookmakers": []}, status=200, request_url="https://example.test")


class PoisonClient:
    def event_odds(self, *args, **kwargs):
        raise OddsApiError("unsupported market", status_code=422, retryable=False)


class SuccessfulOddsClient:
    def event_odds(self, sport_key, event_id, markets, **kwargs):
        return ApiResponse(
            data={
                "id": event_id,
                "sport_key": sport_key,
                "commence_time": "2026-08-14T14:00:00Z",
                "home_team": "Home",
                "away_team": "Away",
                "bookmakers": [
                    {
                        "key": "new_book",
                        "title": "New Book",
                        "markets": [
                            {
                                "key": "new_runtime_market",
                                "outcomes": [
                                    {"name": "Home", "price": 2.0},
                                    {"name": "Away", "price": 2.0},
                                ],
                            }
                        ],
                    }
                ],
            },
            status=200,
            request_url="https://example.test/odds",
        )


class RepaintedOddsClient(SuccessfulOddsClient):
    def event_odds(self, sport_key, event_id, markets, **kwargs):
        response = super().event_odds(sport_key, event_id, markets, **kwargs)
        return ApiResponse(
            data={**response.data, "home_team": "Different Home"},
            status=response.status,
            request_url=response.request_url,
        )


class GlobalCoverageClient:
    def __init__(self):
        self.calls = []

    def event_markets(self, sport_key, event_id, *, regions):
        self.calls.append(tuple(regions))
        books = [
            {
                "key": f"book_{index:03d}",
                "title": f"Book {index}",
                "markets": [
                    {"key": key}
                    for key in (*SOCCER_MARKET_SEEDS, "provider_future_market")
                ],
            }
            for index in range(99)
        ]
        return ApiResponse(
            data={"bookmakers": books},
            status=200,
            request_url="https://example.test/all-regions",
        )


class FailedEnqueueStore:
    def __init__(self, event):
        self.event = event
        self.released = []
        self.marked = []

    def active_events_between(self, start, end):
        return [self.event]

    def get_collection_window(self, match_day):
        return None

    def claim_job(self, claim, expires_at):
        return True

    def enqueue(self, payload):
        raise RuntimeError("simulated SQS failure")

    def release_job(self, claim):
        self.released.append(claim)

    def mark_dispatched(self, event_key, observed_at):
        self.marked.append(event_key)


def event_at(commence_time="2026-08-14T14:00:00Z"):
    sport_key = "soccer_new_runtime_league"
    event_id = "a" * 32
    return {
        "event_key": stable_event_key(sport_key, event_id),
        "event_id": event_id,
        "sport_key": sport_key,
        "commence_time": commence_time,
        "home_team": "Home",
        "away_team": "Away",
        "schedule_revision": 1,
    }


class DeepGateTests(unittest.TestCase):
    def test_direct_job_cannot_call_markets_before_window(self) -> None:
        row = event_at()
        store = FakeStore(row)
        client = FakeClient()
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T03:59:59Z"):
            with self.assertRaisesRegex(RuntimeError, "blocked before daily T-10"):
                _discover_event(store, client, {"event": row})
        self.assertEqual(client.market_calls, 0)
        self.assertEqual(store.calls, [])

    def test_boundary_opens_all_regions_and_runtime_markets(self) -> None:
        row = event_at()
        store = FakeStore(row)
        client = FakeClient()
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _discover_event(store, client, {"event": row})
        self.assertEqual(client.market_calls, 1)
        self.assertEqual(len(store.calls), 1)
        self.assertIn(("event_markets", 4), store.budget_admissions)
        self.assertGreaterEqual(result["market_scope"], 40)
        self.assertGreater(len(store.jobs), 0)

    def test_stale_schedule_job_is_rejected_before_provider_call(self) -> None:
        current = event_at("2026-08-14T16:00:00Z")
        stale = {**current, "commence_time": "2026-08-14T14:00:00Z"}
        store = FakeStore(current)
        client = FakeClient()
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T06:00:00Z"):
            with self.assertRaisesRegex(RuntimeError, "stale soccer collection job"):
                _discover_event(store, client, {"event": stale})
        self.assertEqual(client.market_calls, 0)

    def test_deep_gate_blocks_paid_market_discovery_at_kickoff(self) -> None:
        row = event_at()
        store = FakeStore(row)
        client = FakeClient()
        with patch("soccer_auto.collector._observed_at", return_value=row["commence_time"]):
            with self.assertRaisesRegex(EventAlreadyStarted, "at/after kickoff"):
                _discover_event(store, client, {"event": row})
        self.assertEqual(client.market_calls, 0)
        self.assertEqual(store.calls, [])

    def test_worker_acknowledges_post_kickoff_job_without_dlq_retry(self) -> None:
        row = event_at()
        delivery = {
            "Records": [
                {
                    "messageId": "late-job",
                    "body": "{}",
                }
            ]
        }
        closed = EventAlreadyStarted(
            row["event_key"], row["commence_time"], row["commence_time"]
        )
        with patch("soccer_auto.collector.SoccerStore", return_value=object()), patch(
            "soccer_auto.collector._client", return_value=object()
        ), patch("soccer_auto.collector.process_job", side_effect=closed):
            result = worker_handler(delivery, None)
        self.assertEqual(result["batchItemFailures"], [])
        self.assertEqual(result["processed"][0]["reason"], "EVENT_ALREADY_STARTED")

    def test_runtime_player_market_is_never_dropped_for_new_league(self) -> None:
        keys = _market_keys_for_sport("soccer_new_runtime_league", ["player_new_metric", "new_market"])
        self.assertIn("player_new_metric", keys)
        self.assertIn("new_market", keys)

    def test_poison_market_batch_is_bisected_without_losing_good_scopes(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = {
            "event": row,
            "bookmakers": ["book"],
            "markets": ["good_one", "bad", "good_two", "good_three"],
        }
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _fetch_event(store, PoisonClient(), job)
        self.assertTrue(result["split"])
        self.assertEqual([child["markets"] for child in store.jobs], [["good_one", "bad"], ["good_two", "good_three"]])
        self.assertEqual(store.failures, [])

    def test_unsupported_singleton_is_visible_and_incomplete(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = {"event": row, "bookmakers": ["book"], "markets": ["bad"]}
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _fetch_event(store, PoisonClient(), job)
        self.assertTrue(result["quarantined"])
        self.assertEqual(len(store.failures), 1)
        self.assertTrue(store.failures[0]["permanent"])

    def test_market_first_seen_in_odds_is_added_to_next_inventory_cycle(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = {
            "event": row,
            "bookmakers": ["new_book"],
            "markets": ["new_runtime_market"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
        }
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:01Z"):
            result = _fetch_event(store, SuccessfulOddsClient(), job)
        self.assertTrue(result["canonical_promoted"])
        inventory = store.inventory_writes[-1][1]
        self.assertEqual(inventory["new_book"]["markets"], ["new_runtime_market"])
        self.assertEqual(
            store.coverage_fetches[-1][1]["plan_observed_at"],
            "2026-08-14T04:00:00Z",
        )

    def test_snapshot_timestamp_is_captured_after_provider_response(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = {
            "event": row,
            "bookmakers": ["new_book"],
            "markets": ["new_runtime_market"],
        }
        with patch(
            "soccer_auto.collector._observed_at",
            side_effect=["2026-08-14T13:14:59Z", "2026-08-14T13:15:01Z"],
        ):
            _fetch_event(store, SuccessfulOddsClient(), job)
        snapshot = store.snapshot_writes[-1]
        self.assertEqual(snapshot["observed_at"], "2026-08-14T13:15:01Z")
        self.assertEqual(
            snapshot["request_metadata"]["request_started_at"],
            "2026-08-14T13:14:59Z",
        )
        self.assertEqual(
            snapshot["request_metadata"]["response_observed_at"],
            "2026-08-14T13:15:01Z",
        )

    def test_provider_identity_change_is_archived_but_never_canonicalized(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = {
            "event": row,
            "bookmakers": ["new_book"],
            "markets": ["new_runtime_market"],
        }
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T13:00:00Z"):
            result = _fetch_event(store, RepaintedOddsClient(), job)
        self.assertTrue(result["quarantined"])
        self.assertEqual(result["reason"], "PROVIDER_RESPONSE_SCHEDULE_IDENTITY_MISMATCH")
        self.assertEqual(store.snapshot_writes, [])
        self.assertEqual(store.inventory_writes, [])
        self.assertFalse(store.failures[-1]["permanent"])

    def test_sqs_failure_releases_boundary_claim_without_marking_dispatch(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": None,
        }
        store = FailedEnqueueStore(row)
        boundary = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=boundary
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated SQS failure"):
                dispatch_handler({}, None)
        self.assertEqual(len(store.released), 1)
        self.assertEqual(store.marked, [])

    def test_dispatch_never_requests_post_kickoff_events(self) -> None:
        class EmptyStore:
            def __init__(self):
                self.range = None

            def active_events_between(self, start, end):
                self.range = (start, end)
                return []

            def get_collection_window(self, match_day):
                return None

        store = EmptyStore()
        observed = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=observed
        ):
            result = dispatch_handler({}, None)
        self.assertEqual(store.range[0], "2026-08-14T12:00:00Z")
        self.assertEqual(result["events_seen"], 0)

    def test_global_book_market_fanout_is_complete_and_bounded(self) -> None:
        row = event_at()
        store = FakeStore(row)
        client = GlobalCoverageClient()
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _discover_event(store, client, {"event": row})
        markets = {market for job in store.jobs for market in job.get("markets") or []}
        expected_jobs = (result["market_scope"] + 9) // 10
        self.assertEqual(client.calls, [tuple(ALL_BOOKMAKER_REGIONS)])
        self.assertEqual(result["bookmakers"], 99)
        self.assertIn("provider_future_market", markets)
        self.assertEqual(len(store.jobs), expected_jobs)
        self.assertLessEqual(len(store.jobs), 10)
        self.assertTrue(
            all(job.get("regions") == list(ALL_BOOKMAKER_REGIONS) for job in store.jobs)
        )


if __name__ == "__main__":
    unittest.main()
