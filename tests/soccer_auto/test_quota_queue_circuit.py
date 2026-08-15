from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.collector import _coverage_batch_digest, dispatch_handler, worker_handler
from soccer_auto.config import ALL_BOOKMAKER_REGIONS
from tests.soccer_auto.test_collection_gate import FailedEnqueueStore, FakeStore, event_at


class QuotaBlockedDispatchStore(FailedEnqueueStore):
    def provider_budget_admission(self, operation, observed_at, *, estimated_cost):
        return {
            "available": False,
            "reason": "ATOMIC_SOCCER_ALLOWANCE_EXHAUSTED",
            "external_capacity": True,
        }


class QuotaQueueCircuitTests(unittest.TestCase):
    def test_dispatch_circuit_breaker_does_not_enqueue_paid_work(self) -> None:
        row = {
            **event_at(),
            "last_seen_at": "2026-08-14T04:00:00Z",
            "last_dispatched_at": None,
        }
        store = QuotaBlockedDispatchStore(row)
        observed = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector.now_utc", return_value=observed
        ):
            result = dispatch_handler({}, None)
        self.assertTrue(result["quota_deferred"])
        self.assertEqual(result["enqueued"], 0)
        self.assertEqual(store.discovery_attempts, [])
        self.assertEqual(store.marked, [])

    def test_external_quota_delivery_retires_after_bounded_retries(self) -> None:
        row = event_at()
        store = FakeStore(row)
        store.budget_available = False
        bare = {
            "bookmakers": [],
            "regions": list(ALL_BOOKMAKER_REGIONS),
            "markets": ["h2h"],
            "planned_pairs": ["book|h2h"],
            "plan_digest": "plan",
        }
        job = {
            "version": "soccer-auto-collection-job-v1",
            "action": "FETCH_EVENT",
            "event": row,
            **bare,
            "discovery_observed_at": "2026-08-14T04:00:00Z",
            "batch_digest": _coverage_batch_digest(bare),
            "quota_deferral_count": 2,
        }
        delivery = {
            "Records": [
                {
                    "messageId": "quota-job-circuit",
                    "attributes": {"ApproximateReceiveCount": "1"},
                    "body": json.dumps(job),
                }
            ]
        }
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector._client", return_value=object()
        ), patch(
            "soccer_auto.collector._observed_at",
            return_value="2026-08-14T04:00:01Z",
        ):
            result = worker_handler(delivery, None)
        self.assertEqual(result["batchItemFailures"], [])
        self.assertEqual(store.jobs, [])
        self.assertFalse(result["processed"][0]["retry_reenqueued"])
        self.assertTrue(result["processed"][0]["retry_via_dispatcher"])
        self.assertEqual(result["processed"][0]["quota_deferral_count"], 3)
        self.assertEqual(store.calls, [])


if __name__ == "__main__":
    unittest.main()
