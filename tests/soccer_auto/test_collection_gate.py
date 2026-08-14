from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.canonical import schedule_identity, stable_event_key  # noqa: E402
from soccer_auto.collector import (  # noqa: E402
    CoverageExecutionDeferred,
    EventAlreadyStarted,
    ProviderBudgetDeferred,
    _coverage_batch_digest,
    _discover_event,
    _enqueue_coverage_fanout,
    _fetch_event,
    _fresh_schedule_events,
    _market_keys_for_sport,
    dispatch_handler,
    process_job,
    worker_handler,
)
from soccer_auto.odds_api import ApiResponse, OddsApiError  # noqa: E402
from soccer_auto.config import ALL_BOOKMAKER_REGIONS, SOCCER_MARKET_SEEDS  # noqa: E402
from soccer_auto.storage import COVERAGE_PLAN_VERSION  # noqa: E402


class FakeStore:
    def __init__(self, event, persisted_window=None):
        self.event = event
        self.persisted_window = persisted_window
        self.calls = []
        self.jobs = []
        self.job_delays = []
        self.failures = []
        self.inventory_writes = []
        self.coverage_fetches = []
        self.snapshot_writes = []
        self.budget_admissions = []
        self.coverage_plans = []
        self.discovery_attempts = []
        self.deferred_messages = []
        self.claims = []
        self.released_claims = []
        self.budget_available = True
        self.plan_current = True
        self.claim_available = True
        self.discovery_claim_available = True

    def get_event(self, event_key):
        return self.event

    def active_events_between(self, start, end):
        return [self.event]

    def authoritative_active_events_between(self, start, end, *, observed_at):
        return [self.event], {
            "valid": True,
            "authority_version": "soccer-auto-event-inventory-authority-v1",
            "generation_id": "inventory-test",
            "completed_at": observed_at,
            "authority_revision": 2,
            "reason": "",
        }

    def get_collection_window(self, match_day):
        return self.persisted_window

    def record_collection_window_call(self, event, window, observed_at):
        self.calls.append((event, window, observed_at))

    def provider_budget_available(self, operation, observed_at, estimated_cost=1):
        self.budget_admissions.append((operation, estimated_cost))
        return self.budget_available

    def record_quota(self, *args, **kwargs):
        pass

    def archive_json(self, *args, **kwargs):
        return "s3://soccer/raw.json", "hash"

    def cumulative_market_inventory(self, event_key, **kwargs):
        return {}

    def put_market_inventory(self, event_key, payload, observed_at):
        self.inventory_writes.append((event_key, payload, observed_at))

    def put_coverage_plan(
        self, event_key, payload, observed_at, *, required_inventory=None, event=None,
        discovery_observed_at=None,
        request_markets=(),
    ):
        expected_pairs = sorted(
            f"{book}|{market}"
            for book, detail in payload.items()
            for market in detail.get("markets") or []
        )
        plan = {
            "event_key": event_key,
            "plan_observed_at": observed_at,
            "plan_digest": "test-plan-digest",
            "plan_version": COVERAGE_PLAN_VERSION,
            "expected_pairs": expected_pairs,
            "latest_summary_updated": True,
            "discovery_observed_at": discovery_observed_at or observed_at,
            "schedule_identity": (
                event.get("schedule_identity") or schedule_identity(event)
                if event
                else None
            ),
            "schedule_revision": int((event or {}).get("schedule_revision") or 0),
            "request_markets": list(request_markets),
        }
        self.coverage_plans.append(
            {
                **plan,
                "inventory": payload,
                "required_inventory": required_inventory,
            }
        )
        return plan

    def put_coverage_discovery_attempt(self, event, **kwargs):
        self.discovery_attempts.append({"event": dict(event), **kwargs})
        return {
            "event_key": event["event_key"],
            "discovery_observed_at": kwargs["discovery_observed_at"],
            "latest_summary_updated": True,
        }

    def coverage_plan_is_current(self, event_key, **kwargs):
        return self.plan_current

    def put_coverage_fanout_expected(self, event_key, **kwargs):
        return {
            "latest_summary_updated": True,
            "fanout_enqueued_batch_digests": [],
        }

    def mark_coverage_fanout_enqueued(self, event_key, **kwargs):
        return {"latest_summary_updated": True}

    def complete_coverage_fanout(self, event_key, **kwargs):
        return {"latest_summary_updated": True}

    def claim_job(self, claim, expires_at):
        self.claims.append(claim)
        return self.claim_available

    def begin_coverage_fetch_execution(self, **kwargs):
        self.claims.append(kwargs["batch_digest"])
        if not self.claim_available:
            return {"acquired": False, "state": "COMPLETED"}
        return {
            "acquired": True,
            "state": "IN_PROGRESS",
            "execution_token": kwargs["execution_token"],
        }

    def begin_coverage_discovery_execution(self, **kwargs):
        self.claims.append(f"DISCOVERY#{kwargs['discovery_observed_at']}")
        if not self.discovery_claim_available:
            return {"acquired": False, "state": "IN_PROGRESS"}
        return {
            "acquired": True,
            "state": "IN_PROGRESS",
            "execution_token": kwargs["execution_token"],
        }

    def complete_coverage_discovery_execution(self, **kwargs):
        return True

    def release_coverage_discovery_execution(self, **kwargs):
        self.released_claims.append(
            f"DISCOVERY#{kwargs['discovery_observed_at']}"
        )

    def complete_coverage_fetch_execution(self, **kwargs):
        return bool(
            self.coverage_fetches
            and self.coverage_fetches[-1][1].get("outcome")
            in {
                "HTTP_200",
                "REQUEST_REJECTED",
                "RESPONSE_INVALID",
                "SPLIT_PENDING",
            }
        )

    def release_coverage_fetch_execution(self, **kwargs):
        self.released_claims.append(kwargs["batch_digest"])

    def release_job(self, claim):
        self.released_claims.append(claim)

    def put_coverage_fetch(self, *args, **kwargs):
        self.coverage_fetches.append((args, kwargs))
        return {"latest_summary_updated": self.plan_current}

    def put_snapshot_attempt(self, **kwargs):
        self.snapshot_writes.append(kwargs)
        return {"event_key": kwargs["event"]["event_key"], "canonical_promoted": True}

    def record_collection_failure(self, **payload):
        self.failures.append(payload)

    def enqueue(self, payload, **kwargs):
        self.jobs.append(payload)
        self.job_delays.append(int(kwargs.get("delay_seconds") or 0))

    def defer_message(self, receipt_handle, *, visibility_seconds):
        self.deferred_messages.append((receipt_handle, visibility_seconds))


class FakeClient:
    def __init__(self):
        self.market_calls = 0

    def event_markets(self, sport_key, event_id, *, regions):
        self.market_calls += 1
        return ApiResponse(data={"bookmakers": []}, status=200, request_url="https://example.test")


class FanoutStore:
    def __init__(self):
        self.expected = set()
        self.enqueued = set()
        self.sent = []
        self.fail_mark_once = True

    def put_coverage_fanout_expected(self, event_key, **kwargs):
        incoming = set(kwargs["batch_digests"])
        if self.expected and self.expected != incoming:
            return {"latest_summary_updated": False}
        self.expected = incoming
        return {
            "latest_summary_updated": True,
            "fanout_enqueued_batch_digests": sorted(self.enqueued),
        }

    def enqueue(self, job):
        self.sent.append(dict(job))

    def mark_coverage_fanout_enqueued(self, event_key, **kwargs):
        if self.fail_mark_once:
            self.fail_mark_once = False
            raise RuntimeError("crash after SQS send")
        self.enqueued.add(kwargs["batch_digest"])
        return {"latest_summary_updated": True}

    def complete_coverage_fanout(self, event_key, **kwargs):
        return {"latest_summary_updated": self.enqueued == self.expected}


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


class CurrentInventoryClient:
    def event_markets(self, sport_key, event_id, *, regions):
        return ApiResponse(
            data={
                "bookmakers": [
                    {
                        "key": "current_book",
                        "title": "Current Book",
                        "markets": [{"key": "h2h"}],
                    }
                ]
            },
            status=200,
            request_url="https://example.test/current-markets",
        )


class FailedEnqueueStore:
    def __init__(self, event):
        self.event = event
        self.released = []
        self.marked = []
        self.discovery_attempts = []
        self.manifests = []

    def active_events_between(self, start, end):
        return [self.event]

    def authoritative_active_events_between(self, start, end, *, observed_at):
        return [self.event], {
            "valid": True,
            "authority_version": "soccer-auto-event-inventory-authority-v1",
            "generation_id": "inventory-test",
            "completed_at": observed_at,
            "authority_revision": 2,
            "reason": "",
        }

    def get_collection_window(self, match_day):
        return None

    def latest_coverage_summary(self, event_key):
        return {}

    def claim_job(self, claim, expires_at):
        return True

    def enqueue(self, payload):
        raise RuntimeError("simulated SQS failure")

    def put_coverage_dispatch_manifest(
        self, entries, *, observed_at, inventory_authority
    ):
        self.manifests.append((list(entries), observed_at))
        return {
            "latest_manifest_updated": True,
            "manifest_digest": "manifest",
            "event_count": len(entries),
        }

    def put_coverage_discovery_attempt(self, event, **kwargs):
        self.discovery_attempts.append(kwargs)
        return {"latest_summary_updated": True}

    def release_job(self, claim):
        self.released.append(claim)

    def mark_dispatched(self, event_key, observed_at, **kwargs):
        self.marked.append(event_key)
        return True


class RecoverablePlanDispatchStore(FailedEnqueueStore):
    def __init__(self, event, summary):
        super().__init__(event)
        self.summary = dict(summary)
        self.jobs = []

    def latest_coverage_summary(self, event_key):
        return dict(self.summary)

    def enqueue(self, payload):
        self.jobs.append(dict(payload))

    def put_coverage_discovery_attempt(self, event, **kwargs):
        self.discovery_attempts.append(kwargs)
        self.summary = {
            **self.summary,
            "discovery_observed_at": kwargs["discovery_observed_at"],
            "discovery_status": kwargs["status"],
            "discovery_status_observed_at": kwargs["observed_at"],
        }
        return {**self.summary, "latest_summary_updated": True}

    def mark_dispatched(self, event_key, observed_at, **kwargs):
        self.marked.append(event_key)
        self.event["last_dispatched_at"] = observed_at
        return True


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


def fetch_job(
    event,
    *,
    bookmakers,
    markets,
    planned_pairs=None,
    regions=None,
    plan_digest="plan",
    plan_observed_at="2026-08-14T04:00:00Z",
):
    job = {
        "event": event,
        "bookmakers": list(bookmakers),
        "regions": list(regions or ()),
        "markets": list(markets),
        "planned_pairs": list(
            planned_pairs
            if planned_pairs is not None
            else [f"{book}|{market}" for book in bookmakers for market in markets]
        ),
        "discovery_observed_at": plan_observed_at,
        "plan_digest": plan_digest,
    }
    job["batch_digest"] = _coverage_batch_digest(job)
    return job


class DeepGateTests(unittest.TestCase):
    def test_fixture_ages_out_only_after_three_successful_inventory_omissions(self) -> None:
        observed = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)
        stale_seen = {
            **event_at("2026-08-14T14:00:00Z"),
            "last_seen_at": "2026-08-14T00:00:00Z",
            "inventory_omission_count": 0,
        }
        confirmed_absent = {**stale_seen, "inventory_omission_count": 3}
        self.assertEqual(_fresh_schedule_events([stale_seen], observed), [stale_seen])
        self.assertEqual(_fresh_schedule_events([confirmed_absent], observed), [])

    def test_fanout_resends_after_send_before_evidence_without_losing_batches(self) -> None:
        row = event_at()
        store = FanoutStore()
        markets = [f"market_{index}" for index in range(11)]
        plan = {
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "plan_observed_at": "2026-08-14T04:00:01Z",
            "plan_digest": "plan",
            "request_markets": markets,
            "required_pairs": [f"book|{market}" for market in markets],
            "probe_pairs": [],
        }
        with self.assertRaisesRegex(RuntimeError, "crash after SQS send"):
            _enqueue_coverage_fanout(
                store,
                row,
                plan,
                observed_at="2026-08-14T04:00:02Z",
            )
        result = _enqueue_coverage_fanout(
            store,
            row,
            plan,
            observed_at="2026-08-14T04:00:03Z",
        )
        sent_digests = [job["batch_digest"] for job in store.sent]
        self.assertEqual(len(store.expected), 2)
        self.assertEqual(set(sent_digests), store.expected)
        self.assertEqual(len(sent_digests), 3)
        self.assertEqual(result["fetch_jobs_enqueued"], 2)
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
            result = _discover_event(store, client, {"event": stale})
        self.assertEqual(result["reason"], "STALE_EVENT_SCHEDULE")
        self.assertEqual(client.market_calls, 0)
        self.assertEqual(store.discovery_attempts, [])

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
        job = fetch_job(
            row,
            bookmakers=["book"],
            markets=["good_one", "bad", "good_two", "good_three"],
            planned_pairs=[
                "book|good_one",
                "book|bad",
                "book|good_two",
                "book|good_three",
            ],
        )
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _fetch_event(store, PoisonClient(), job)
        self.assertTrue(result["split"])
        self.assertEqual([child["markets"] for child in store.jobs], [["good_one", "bad"], ["good_two", "good_three"]])
        self.assertEqual(
            [child["planned_pairs"] for child in store.jobs],
            [["book|bad", "book|good_one"], ["book|good_three", "book|good_two"]],
        )
        self.assertEqual(store.failures, [])

    def test_quota_denial_is_not_acknowledged_as_completed_work(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.budget_available = False
        delivery = {
            "Records": [
                {
                    "messageId": "quota-job",
                    "receiptHandle": "receipt",
                    "attributes": {"ApproximateReceiveCount": "2"},
                    "body": json.dumps(
                        {
                            "version": "soccer-auto-collection-job-v1",
                            "action": "FETCH_EVENT",
                            "event": row,
                            "bookmakers": [],
                            "regions": list(ALL_BOOKMAKER_REGIONS),
                            "markets": ["h2h"],
                            "planned_pairs": ["book|h2h"],
                            "discovery_observed_at": "2026-08-14T04:00:00Z",
                            "plan_digest": "plan",
                            "batch_digest": _coverage_batch_digest(
                                {
                                    "bookmakers": [],
                                    "regions": list(ALL_BOOKMAKER_REGIONS),
                                    "markets": ["h2h"],
                                    "planned_pairs": ["book|h2h"],
                                    "plan_digest": "plan",
                                }
                            ),
                        }
                    ),
                }
            ]
        }
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector._client", return_value=object()
        ), patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:01Z"):
            result = worker_handler(delivery, None)
        self.assertEqual(result["batchItemFailures"], [])
        self.assertEqual(store.deferred_messages, [])
        self.assertEqual(store.job_delays, [60])
        self.assertEqual(store.jobs[0]["quota_deferral_count"], 2)
        self.assertTrue(result["processed"][0]["retry_reenqueued"])
        self.assertEqual(store.calls, [])
        self.assertEqual(store.coverage_fetches[-1][1]["outcome"], "QUOTA_DEFERRED")
        self.assertFalse(store.coverage_fetches[-1][1]["absence_scope_complete"])

    def test_discovery_quota_denial_keeps_the_due_event_visible(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.budget_available = False
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            with self.assertRaises(ProviderBudgetDeferred):
                _discover_event(
                    store,
                    object(),
                    {
                        "event": row,
                        "dispatch_observed_at": "2026-08-14T04:00:00Z",
                    },
                )
        self.assertEqual(
            [attempt["status"] for attempt in store.discovery_attempts],
            ["STARTED", "QUOTA_DEFERRED"],
        )

    def test_internal_budget_observation_failure_is_not_external_quota(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.provider_budget_admission = lambda *args, **kwargs: {
            "available": False,
            "reason": "QUOTA_OBSERVATION_UNAVAILABLE",
            "external_capacity": False,
        }
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            with self.assertRaises(CoverageExecutionDeferred):
                _fetch_event(
                    store,
                    object(),
                    fetch_job(
                        row,
                        bookmakers=[],
                        regions=ALL_BOOKMAKER_REGIONS,
                        markets=["h2h"],
                        planned_pairs=["book|h2h"],
                    ),
                )
        attempt = store.coverage_fetches[-1][1]
        self.assertEqual(attempt["outcome"], "RETRYABLE_ERROR")
        self.assertEqual(attempt["budget_reason"], "QUOTA_OBSERVATION_UNAVAILABLE")
        self.assertFalse(attempt["absence_scope_complete"])

    def test_paid_work_defers_while_inventory_generation_is_unfenced(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.authoritative_active_events_between = lambda *args, **kwargs: (
            [row],
            {
                "valid": False,
                "reason": "INVENTORY_GENERATION_NOT_COMPLETED",
            },
        )
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            with self.assertRaises(CoverageExecutionDeferred):
                _discover_event(store, object(), {"event": row})
        self.assertEqual(store.budget_admissions, [])
        self.assertEqual(store.calls, [])

    def test_legacy_discover_sport_job_cannot_bypass_inventory_fence(self) -> None:
        row = event_at()
        store = FakeStore(row)
        result = process_job(
            {
                "version": "soccer-auto-collection-job-v1",
                "action": "DISCOVER_SPORT",
                "sport_key": row["sport_key"],
            },
            store=store,
            client=object(),
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "EVENT_INVENTORY_HANDLER_ONLY")
        self.assertEqual(store.jobs, [])

    def test_stale_fetch_is_acknowledged_before_budget_or_provider_work(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.plan_current = False
        store.budget_available = False
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            result = _fetch_event(
                store,
                object(),
                fetch_job(
                    row,
                    bookmakers=[],
                    regions=ALL_BOOKMAKER_REGIONS,
                    markets=["h2h"],
                    planned_pairs=["book|h2h"],
                    plan_digest="stale-plan",
                ),
            )
        self.assertEqual(result["reason"], "STALE_COVERAGE_PLAN")
        self.assertFalse(result["provider_called"])
        self.assertEqual(store.budget_admissions, [])
        self.assertEqual(store.coverage_fetches, [])

    def test_missing_or_tampered_batch_provenance_never_spends_provider_credit(self) -> None:
        row = event_at()
        store = FakeStore(row)
        missing = {
            "event": row,
            "bookmakers": ["book"],
            "markets": ["h2h"],
        }
        tampered = fetch_job(row, bookmakers=["book"], markets=["h2h"])
        tampered["batch_digest"] = "tampered"
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            results = [
                _fetch_event(store, object(), missing),
                _fetch_event(store, object(), tampered),
            ]
        self.assertEqual(
            [row["reason"] for row in results],
            ["INVALID_COVERAGE_BATCH_PROVENANCE"] * 2,
        )
        self.assertEqual(store.budget_admissions, [])

    def test_duplicate_fetch_batch_is_acknowledged_without_provider_spend(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.claim_available = False
        job = fetch_job(row, bookmakers=["book"], markets=["h2h"])
        result = _fetch_event(store, object(), job)
        self.assertEqual(result["reason"], "COVERAGE_BATCH_ALREADY_COMPLETED")
        self.assertFalse(result["provider_called"])
        self.assertEqual(store.budget_admissions, [])

    def test_unsupported_singleton_is_visible_and_incomplete(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = fetch_job(row, bookmakers=["book"], markets=["bad"])
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            result = _fetch_event(store, PoisonClient(), job)
        self.assertTrue(result["quarantined"])
        self.assertEqual(len(store.failures), 1)
        self.assertTrue(store.failures[0]["permanent"])

    def test_market_first_seen_in_odds_is_added_to_next_inventory_cycle(self) -> None:
        row = event_at()
        store = FakeStore(row)
        job = fetch_job(
            row,
            bookmakers=["new_book"],
            markets=["new_runtime_market"],
        )
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
        job = fetch_job(
            row,
            bookmakers=["new_book"],
            markets=["new_runtime_market"],
        )
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
        job = fetch_job(
            row,
            bookmakers=["new_book"],
            markets=["new_runtime_market"],
        )
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T13:00:00Z"):
            result = _fetch_event(store, RepaintedOddsClient(), job)
        self.assertTrue(result["quarantined"])
        self.assertEqual(result["reason"], "PROVIDER_RESPONSE_SCHEDULE_IDENTITY_MISMATCH")
        self.assertEqual(store.snapshot_writes, [])
        self.assertEqual(store.inventory_writes, [])
        self.assertFalse(store.failures[-1]["permanent"])

    def test_sqs_failure_remains_redispatchable_without_marking_dispatch(self) -> None:
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
        self.assertEqual(store.released, [])
        self.assertEqual(store.marked, [])
        self.assertEqual(
            [attempt["status"] for attempt in store.discovery_attempts],
            ["QUEUED", "ENQUEUE_FAILED"],
        )

    def test_dispatch_recovers_current_plan_before_normal_cadence(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": "2026-08-14T04:00:00Z",
        }
        row["schedule_identity"] = schedule_identity(row)
        summary = {
            "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
            "event_key": row["event_key"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "PLAN_READY",
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": "2026-08-14T04:00:30Z",
            "plan_digest": "saved-plan",
            "request_markets": ["h2h", "totals"],
            "schedule_revision": row["schedule_revision"],
            "schedule_identity": row["schedule_identity"],
        }
        store = RecoverablePlanDispatchStore(row, summary)
        observed = datetime(2026, 8, 14, 4, 1, tzinfo=timezone.utc)

        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=observed
        ):
            result = dispatch_handler({}, None)

        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(result["recovered_plans"], 1)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(store.marked, [])
        self.assertEqual(len(store.jobs), 1)
        self.assertEqual(
            store.jobs[0]["dispatch_observed_at"],
            "2026-08-14T04:00:00Z",
        )
        self.assertEqual(
            store.manifests[0][0][0]["required_discovery_observed_at"],
            "2026-08-14T04:00:00Z",
        )

    def test_dispatch_recovers_queued_existing_plan_after_send_gap(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": "2026-08-14T04:00:00Z",
        }
        row["schedule_identity"] = schedule_identity(row)
        summary = {
            "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
            "event_key": row["event_key"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "QUEUED",
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": "2026-08-14T04:00:30Z",
            "plan_digest": "saved-plan",
            "request_markets": ["h2h", "totals"],
            "schedule_revision": row["schedule_revision"],
            "schedule_identity": row["schedule_identity"],
        }
        store = RecoverablePlanDispatchStore(row, summary)
        observed = datetime(2026, 8, 14, 4, 2, tzinfo=timezone.utc)

        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=observed
        ):
            result = dispatch_handler({}, None)

        self.assertEqual(result["enqueued"], 1)
        self.assertEqual(result["recovered_plans"], 1)
        self.assertEqual(len(store.jobs), 1)
        self.assertEqual(
            store.jobs[0]["dispatch_observed_at"],
            "2026-08-14T04:00:00Z",
        )

    def test_dispatch_uses_new_generation_after_plan_cadence_expires(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": "2026-08-14T04:00:00Z",
        }
        row["schedule_identity"] = schedule_identity(row)
        summary = {
            "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
            "event_key": row["event_key"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "PLAN_READY",
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": "2026-08-14T04:00:30Z",
            "plan_digest": "saved-plan",
            "request_markets": ["h2h", "totals"],
            "schedule_revision": row["schedule_revision"],
            "schedule_identity": row["schedule_identity"],
        }
        store = RecoverablePlanDispatchStore(row, summary)
        observed_ticks = [
            datetime(2026, 8, 14, 4, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 14, 4, 14, tzinfo=timezone.utc),
            datetime(2026, 8, 14, 4, 16, tzinfo=timezone.utc),
        ]
        results = []
        for observed in observed_ticks:
            with patch(
                "soccer_auto.collector.SoccerStore", return_value=store
            ), patch("soccer_auto.collector.now_utc", return_value=observed):
                results.append(dispatch_handler({}, None))

        self.assertEqual(
            [result["recovered_plans"] for result in results], [1, 1, 0]
        )
        self.assertEqual(store.marked, [row["event_key"]])
        self.assertEqual(len(store.jobs), 3)
        self.assertEqual(
            store.jobs[-1]["dispatch_observed_at"],
            "2026-08-14T04:15:00Z",
        )

    def test_dispatch_does_not_republish_completed_plan_within_cadence(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": "2026-08-14T04:00:00Z",
        }
        row["schedule_identity"] = schedule_identity(row)
        summary = {
            "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
            "event_key": row["event_key"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "HTTP_200",
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": "2026-08-14T04:00:30Z",
            "plan_digest": "completed-plan",
            "request_markets": ["h2h", "totals"],
            "schedule_revision": row["schedule_revision"],
            "schedule_identity": row["schedule_identity"],
        }
        store = RecoverablePlanDispatchStore(row, summary)
        observed = datetime(2026, 8, 14, 4, 1, tzinfo=timezone.utc)

        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=observed
        ):
            result = dispatch_handler({}, None)

        self.assertEqual(result["enqueued"], 0)
        self.assertEqual(result["recovered_plans"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(store.jobs, [])
        self.assertEqual(
            store.manifests[0][0][0]["required_discovery_observed_at"], ""
        )

    def test_dispatch_never_requests_post_kickoff_events(self) -> None:
        class EmptyStore:
            def __init__(self):
                self.range = None

            def active_events_between(self, start, end):
                self.range = (start, end)
                return []

            def authoritative_active_events_between(
                self, start, end, *, observed_at
            ):
                self.range = (start, end)
                return [], {
                    "valid": True,
                    "authority_version": "soccer-auto-event-inventory-authority-v1",
                    "generation_id": "inventory-test",
                    "completed_at": observed_at,
                    "authority_revision": 2,
                    "reason": "",
                }

            def get_collection_window(self, match_day):
                return None

            def put_coverage_dispatch_manifest(
                self, entries, *, observed_at, inventory_authority
            ):
                return {
                    "latest_manifest_updated": True,
                    "manifest_digest": "empty-manifest",
                    "event_count": 0,
                }

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
        planned = [pair for job in store.jobs for pair in job.get("planned_pairs") or []]
        self.assertEqual(len(planned), len(set(planned)))
        self.assertEqual(set(planned), set(store.coverage_plans[-1]["expected_pairs"]))
        for job in store.jobs:
            self.assertTrue(
                all(pair.rsplit("|", 1)[1] in job["markets"] for pair in job["planned_pairs"])
            )

    def test_plan_ready_replay_resumes_five_batches_without_paid_discovery(self) -> None:
        row = event_at()
        store = FakeStore(row)
        client = FakeClient()
        markets = [f"market_{index:02d}" for index in range(48)]
        saved_plan = {
            "event_key": row["event_key"],
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "discovery_status": "STARTED",
            "plan_version": COVERAGE_PLAN_VERSION,
            "plan_observed_at": "2026-08-14T04:00:30Z",
            "plan_digest": "saved-five-batch-plan",
            "request_markets": markets,
            "required_pairs": [f"book|{market}" for market in markets],
            "probe_pairs": [],
            "expected_pairs": [f"book|{market}" for market in markets],
            "latest_summary_updated": True,
        }

        def resume_attempt(event, **kwargs):
            store.discovery_attempts.append({"event": dict(event), **kwargs})
            return dict(saved_plan)

        store.put_coverage_discovery_attempt = resume_attempt
        with patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:01:00Z",
        ):
            result = _discover_event(
                store,
                client,
                {
                    "event": row,
                    "dispatch_observed_at": "2026-08-14T04:00:00Z",
                },
            )

        self.assertTrue(result["resumed_existing_plan"])
        self.assertEqual(result["fetch_jobs_total"], 5)
        self.assertEqual(result["fetch_jobs_enqueued"], 5)
        self.assertEqual(len(store.jobs), 5)
        self.assertEqual(client.market_calls, 0)
        self.assertEqual(store.calls, [])

    def test_rolling_probe_union_is_not_retimestamped_as_current_inventory(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.cumulative_market_inventory = lambda *args, **kwargs: {
            "stale_book": {
                "title": "Stale Book",
                "regions": ["uk"],
                "markets": ["totals"],
            }
        }
        with patch("soccer_auto.collector._observed_at", return_value="2026-08-14T04:00:00Z"):
            _discover_event(store, CurrentInventoryClient(), {"event": row})
        self.assertEqual(set(store.inventory_writes[-1][1]), {"current_book"})
        plan = store.coverage_plans[-1]
        self.assertEqual(set(plan["inventory"]), {"current_book", "stale_book"})
        self.assertEqual(set(plan["required_inventory"]), {"current_book"})


if __name__ == "__main__":
    unittest.main()
