from __future__ import annotations

import io
import os
import threading
import unittest
import urllib.error
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from botocore.exceptions import ClientError  # noqa: E402
from soccer_auto.odds_api import (  # noqa: E402
    DistributedOddsApiRateLimiter,
    OddsApiClient,
    OddsApiError,
    OddsApiRateLimitError,
    _bounded_retry_after,
    provider_safety_config,
)
from soccer_auto.storage import SoccerStore  # noqa: E402


def conditional_failure() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "race"}},
        "UpdateItem",
    )


class FakeClock:
    def __init__(self, now_ms: int = 0):
        self.now_ms = now_ms

    def read(self):
        return self.now_ms

    def sleep(self, seconds):
        self.now_ms += max(1, int(round(float(seconds) * 1000)))


class AtomicTable:
    def __init__(self, *, forced_contention: int = 0):
        self.item = {}
        self.forced_contention = forced_contention
        self.conditions = []
        self.blocked = []
        self.lock = threading.Lock()

    def get_item(self, **kwargs):
        with self.lock:
            return {"Item": dict(self.item)} if self.item else {}

    def update_item(self, **kwargs):
        with self.lock:
            self.conditions.append(kwargs["ConditionExpression"])
            values = kwargs["ExpressionAttributeValues"]
            if self.forced_contention:
                self.forced_contention -= 1
                raise conditional_failure()
            expected = values.get(":expected")
            current = self.item.get("next_allowed_ms")
            if expected is None:
                if current is not None:
                    raise conditional_failure()
            elif current != expected:
                raise conditional_failure()
            self.item.update(
                {
                    "PK": kwargs["Key"]["PK"],
                    "SK": kwargs["Key"]["SK"],
                    "entity_type": values[":entity"],
                    "next_allowed_ms": values[":next"],
                    "last_granted_slot_ms": values[":slot"],
                    "last_operation": values[":operation"],
                    "last_provider_attempt": values[":attempt"],
                    "configured_rps": values[":rps"],
                    "minimum_spacing_ms": values[":spacing"],
                    "burst_capacity": values[":burst"],
                }
            )
        return {}

    def put_item(self, **kwargs):
        self.blocked.append(dict(kwargs["Item"]))


class UnavailableTable:
    def get_item(self, **kwargs):
        raise RuntimeError("DynamoDB unavailable")


class ProviderTelemetryTable:
    def __init__(self):
        self.query_kwargs = None

    def query(self, **kwargs):
        self.query_kwargs = kwargs
        return {
            "Items": [
                {
                    "PK": "PROVIDER_429",
                    "SK": "OBSERVED#2026-08-14T03:00:00.000000Z#newer",
                    "operation": "sports/soccer_epl/events",
                    "attempt": 2,
                },
                {
                    "PK": "PROVIDER_429",
                    "SK": "OBSERVED#2026-08-14T02:00:00.000000Z#older",
                    "operation": "sports",
                    "attempt": 1,
                },
            ],
            "LastEvaluatedKey": {"PK": "PROVIDER_429", "SK": "older"},
        }

    def put_item(self, **kwargs):
        raise RuntimeError("DynamoDB unavailable")


class EmptyResponse:
    status = 200
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"[]"


class CountingLimiter:
    def __init__(self):
        self.calls = []
        self.provider_429s = []

    def acquire(self, *, operation, attempt):
        self.calls.append((operation, attempt))

    def record_provider_429(self, *, operation, attempt, retry_after):
        self.provider_429s.append(
            {
                "operation": operation,
                "attempt": attempt,
                "retry_after": retry_after,
            }
        )


class DistributedRateLimiterTests(unittest.TestCase):
    def test_default_is_three_rps_with_one_globally_smoothed_slot(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOCCER_AUTO_ODDS_RPS_CAP", None)
            config = provider_safety_config()
        self.assertEqual(config["soccer_request_limit_per_second"], 3)
        self.assertEqual(config["burst_capacity"], 1)
        self.assertEqual(config["minimum_spacing_ms"], 334)
        self.assertTrue(config["distributed_lease"])
        self.assertTrue(config["fail_closed"])

    def test_atomic_cas_contention_preserves_rolling_window_spacing(self) -> None:
        clock = FakeClock(999)
        table = AtomicTable(forced_contention=1)
        limiter = DistributedOddsApiRateLimiter(
            table,
            requests_per_second=30,
            max_wait_seconds=2,
            clock_ms=clock.read,
            sleeper=clock.sleep,
        )
        slots = [limiter.acquire(operation="sports", attempt=1) for _ in range(6)]
        self.assertEqual(limiter.requests_per_second, 3)
        self.assertTrue(all(right - left >= 334 for left, right in zip(slots, slots[1:])))
        for start in slots:
            self.assertLessEqual(sum(start <= slot < start + 1000 for slot in slots), 3)
        self.assertIn("attribute_not_exists(next_allowed_ms)", table.conditions)
        self.assertIn("next_allowed_ms=:expected", table.conditions)
        self.assertEqual(table.item["burst_capacity"], 1)
        self.assertEqual(table.item["entity_type"], "SOCCER_DISTRIBUTED_RATE_LIMIT_STATE")

    def test_late_wakeup_claims_at_actual_time_instead_of_replaying_stale_slot(self) -> None:
        class LateClock(FakeClock):
            def sleep(self, seconds):
                super().sleep(seconds)
                self.now_ms += 150

        clock = LateClock(1000)
        table = AtomicTable()
        table.item = {"next_allowed_ms": 1334}
        limiter = DistributedOddsApiRateLimiter(
            table,
            clock_ms=clock.read,
            sleeper=clock.sleep,
        )
        slot = limiter.acquire(operation="sports", attempt=1)
        self.assertEqual(slot, 1484)
        self.assertEqual(table.item["next_allowed_ms"], 1818)

    def test_concurrent_callers_win_atomic_spaced_permits(self) -> None:
        table = AtomicTable()
        barrier = threading.Barrier(4)
        slots = []
        errors = []

        def worker():
            limiter = DistributedOddsApiRateLimiter(table, max_wait_seconds=3)
            try:
                barrier.wait()
                slots.append(limiter.acquire(operation="events", attempt=1))
            except Exception as exc:  # pragma: no cover - failure assertion below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(slots), 4)
        ordered = sorted(slots)
        self.assertTrue(all(right - left >= 334 for left, right in zip(ordered, ordered[1:])))

    def test_every_network_retry_reacquires_a_distributed_slot(self) -> None:
        limiter = CountingLimiter()
        attempts = 0

        def opener(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise urllib.error.URLError("temporary")
            return EmptyResponse()

        client = OddsApiClient("secret", max_attempts=2, opener=opener, limiter=limiter)
        with patch("soccer_auto.odds_api.time.sleep", return_value=None):
            response = client.events("soccer_epl")
        self.assertEqual(response.status, 200)
        self.assertEqual(attempts, 2)
        self.assertEqual(
            limiter.calls,
            [("sports/soccer_epl/events", 1), ("sports/soccer_epl/events", 2)],
        )

    def test_every_provider_429_attempt_records_non_secret_telemetry(self) -> None:
        limiter = CountingLimiter()

        def opener(*args, **kwargs):
            raise urllib.error.HTTPError(
                "https://redacted.invalid",
                429,
                "rate limited",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"EXCEEDED_FREQ_LIMIT"}'),
            )

        client = OddsApiClient(
            "never-persist-this-key",
            max_attempts=2,
            opener=opener,
            limiter=limiter,
        )
        with patch("soccer_auto.odds_api.time.sleep", return_value=None):
            with self.assertRaises(OddsApiError):
                client.events("soccer_epl")
        self.assertEqual(
            limiter.provider_429s,
            [
                {
                    "operation": "sports/soccer_epl/events",
                    "attempt": 1,
                    "retry_after": 0.0,
                },
                {
                    "operation": "sports/soccer_epl/events",
                    "attempt": 2,
                    "retry_after": 0.0,
                },
            ],
        )
        self.assertNotIn("never-persist-this-key", repr(limiter.provider_429s))

    def test_provider_429_row_has_ttl_and_no_url_or_credential(self) -> None:
        table = AtomicTable()
        limiter = DistributedOddsApiRateLimiter(table)
        limiter.record_provider_429(
            operation="sports/soccer_epl/events",
            attempt=2,
            retry_after=1.5,
        )
        row = table.blocked[-1]
        self.assertEqual(row["PK"], "PROVIDER_429")
        self.assertEqual(row["entity_type"], "SOCCER_ODDS_API_PROVIDER_429")
        self.assertEqual(row["operation"], "sports/soccer_epl/events")
        self.assertEqual(row["attempt"], 2)
        self.assertEqual(row["retry_after"], "1.5")
        self.assertGreater(row["expires_at"], 0)
        self.assertNotIn("url", row)
        self.assertNotIn("never-persist-this-key", repr(row))

    def test_provider_429_health_summary_is_bounded_and_returns_latest_rows(self) -> None:
        table = ProviderTelemetryTable()
        store = SoccerStore.__new__(SoccerStore)
        store.ops = table
        status = store.provider_429_status(
            observed_at="2026-08-14T04:00:00Z",
            lookback_hours=24,
            row_limit=1,
            count_limit=2,
        )
        self.assertEqual(status["rolling_count"], 2)
        self.assertTrue(status["count_is_lower_bound"])
        self.assertEqual(status["count_cap"], 2)
        self.assertEqual(len(status["latest_rows"]), 1)
        self.assertEqual(status["latest_rows"][0]["attempt"], 2)
        self.assertFalse(table.query_kwargs["ScanIndexForward"])
        self.assertTrue(table.query_kwargs["ConsistentRead"])
        self.assertEqual(table.query_kwargs["Limit"], 2)

    def test_every_public_endpoint_routes_through_the_same_limiter(self) -> None:
        limiter = CountingLimiter()
        client = OddsApiClient(
            "secret",
            opener=lambda *args, **kwargs: EmptyResponse(),
            limiter=limiter,
        )
        client.sports()
        client.events("soccer_epl")
        client.odds("soccer_epl", ["h2h"])
        client.scores("soccer_epl")
        client.event_markets("soccer_epl", "event-1")
        client.event_odds("soccer_epl", "event-1", ["correct_score"])
        client.historical_odds("soccer_epl", "2026-01-01T00:00:00Z", ["h2h"])
        client.historical_events("soccer_epl", "2026-01-01T00:00:00Z")
        client.historical_event_odds(
            "soccer_epl", "event-1", "2026-01-01T00:00:00Z", ["correct_score"]
        )
        self.assertEqual(len(limiter.calls), 9)
        self.assertTrue(all(attempt == 1 for _, attempt in limiter.calls))

    def test_unavailable_limiter_fails_closed_before_provider_io(self) -> None:
        clock = FakeClock()
        limiter = DistributedOddsApiRateLimiter(
            UnavailableTable(),
            max_wait_seconds=0.02,
            clock_ms=clock.read,
            sleeper=clock.sleep,
        )
        provider_calls = []
        client = OddsApiClient(
            "secret",
            opener=lambda *args, **kwargs: provider_calls.append(args),
            limiter=limiter,
        )
        with patch("builtins.print"):
            with self.assertRaises(OddsApiRateLimitError) as caught:
                client.sports()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(provider_calls, [])

    def test_missing_ops_table_configuration_fails_closed(self) -> None:
        with patch.dict(os.environ, {"SOCCER_AUTO_OPS_TABLE": ""}):
            with self.assertRaises(OddsApiRateLimitError):
                OddsApiClient("secret", opener=lambda *args, **kwargs: EmptyResponse())

    def test_retry_after_is_never_shortened_and_excessive_wait_fails_closed(self) -> None:
        self.assertEqual(_bounded_retry_after({"Retry-After": "2"}), 2.0)
        with self.assertRaisesRegex(Exception, "exceeds bounded"):
            _bounded_retry_after({"Retry-After": "21"})
        with self.assertRaisesRegex(Exception, "invalid Retry-After"):
            _bounded_retry_after({"Retry-After": "not-a-date"})


if __name__ == "__main__":
    unittest.main()
