from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.storage import SoccerStore  # noqa: E402
from soccer_auto.odds_api import ApiResponse  # noqa: E402


class Ops:
    def __init__(self, remaining, used):
        self.blocked = []
        self.observations = []
        self.quota_state = (
            {
                "PK": "QUOTA_STATE",
                "SK": "LATEST",
                "remaining": remaining,
                "used": used,
                "observed_at": "2026-08-14T03:59:00Z",
                "quota_snapshot": "snapshot-1",
            }
            if remaining is not None and used is not None
            else {}
        )
        self.admission = {}

    def get_item(self, *, Key, **kwargs):
        if Key == {"PK": "QUOTA_STATE", "SK": "LATEST"}:
            return {"Item": dict(self.quota_state)} if self.quota_state else {}
        if Key == {"PK": "QUOTA_ADMISSION", "SK": "CURRENT"}:
            return {"Item": dict(self.admission)} if self.admission else {}
        return {}

    def put_item(self, **kwargs):
        item = dict(kwargs["Item"])
        if item.get("PK") == "QUOTA_GUARD":
            self.blocked.append(item)
        elif item.get("PK") == "QUOTA_STATE":
            self.quota_state = item
        elif item.get("PK") == "QUOTA_ADMISSION":
            self.admission = item
        elif item.get("PK") == "QUOTA":
            self.observations.append(item)

    def update_item(self, *, Key, ExpressionAttributeValues, **kwargs):
        if Key != {"PK": "QUOTA_ADMISSION", "SK": "CURRENT"}:
            raise AssertionError(Key)
        self.admission["admitted_credits"] = ExpressionAttributeValues[":next"]
        self.admission["updated_at"] = ExpressionAttributeValues[":at"]


class SharedProviderGuardTests(unittest.TestCase):
    def store(self, remaining, used):
        store = SoccerStore.__new__(SoccerStore)
        store.ops = Ops(remaining, used)
        return store

    def test_coverage_first_default_keeps_full_2000_credit_race_buffer(self) -> None:
        store = self.store(2001, 2999)
        with patch.dict("os.environ", {}, clear=True):
            allowed = store.provider_budget_available(
                "event_odds",
                "2026-08-14T04:00:00Z",
            )
        self.assertTrue(allowed)
        self.assertEqual(store.ops.admission["reserve_credits"], 0.0)
        self.assertEqual(store.ops.admission["race_buffer_credits"], 2000)
        self.assertEqual(store.ops.admission["spendable_credits"], 1)

    def test_coverage_first_default_still_blocks_inside_race_buffer(self) -> None:
        store = self.store(2000, 3000)
        with patch.dict("os.environ", {}, clear=True):
            allowed = store.provider_budget_available(
                "event_odds",
                "2026-08-14T04:00:00Z",
            )
        self.assertFalse(allowed)
        self.assertEqual(store.ops.blocked[0]["reserve_percent"], 0.0)
        self.assertEqual(store.ops.blocked[0]["race_buffer_credits"], 2000)
        self.assertEqual(store.ops.blocked[0]["reason"], "RACE_BUFFER_REACHED")

    def test_soccer_stops_before_shared_subscription_reserve(self) -> None:
        store = self.store(80, 20)
        with patch.dict(
            "os.environ",
            {
                "SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT": "80",
                "SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS": "0",
            },
        ):
            allowed = store.provider_budget_available("event_odds", "2026-08-14T04:00:00Z")
        self.assertFalse(allowed)
        self.assertEqual(len(store.ops.blocked), 1)

    def test_collection_continues_above_reserve(self) -> None:
        store = self.store(82, 18)
        with patch.dict(
            "os.environ",
            {
                "SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT": "80",
                "SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS": "0",
            },
        ):
            allowed = store.provider_budget_available("event_odds", "2026-08-14T04:00:00Z")
        self.assertTrue(allowed)
        self.assertEqual(store.ops.blocked, [])

    def test_costly_collection_fails_closed_until_quota_is_observed(self) -> None:
        store = self.store(None, None)
        allowed = store.provider_budget_available("event_odds", "2026-08-14T04:00:00Z")
        self.assertFalse(allowed)
        self.assertEqual(store.ops.blocked[0]["reason"], "QUOTA_OBSERVATION_UNAVAILABLE")

    def test_default_race_buffer_scales_down_for_small_subscription(self) -> None:
        store = self.store(95, 5)
        with patch.dict(
            "os.environ",
            {
                "SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT": "80",
                "SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS": "50",
            },
        ):
            allowed = store.provider_budget_available("event_odds", "2026-08-14T04:00:00Z")
        self.assertTrue(allowed)

    def test_atomic_admission_prevents_sequential_workers_from_overspending_slice(self) -> None:
        store = self.store(100, 0)
        with patch.dict(
            "os.environ",
            {
                "SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT": "80",
                "SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS": "0",
            },
        ):
            self.assertTrue(store.provider_budget_available("event_odds", "2026-08-14T04:00:00Z", 10))
            self.assertTrue(store.provider_budget_available("event_odds", "2026-08-14T04:00:01Z", 10))
            self.assertFalse(store.provider_budget_available("event_odds", "2026-08-14T04:00:02Z", 1))
        self.assertEqual(store.ops.admission["admitted_credits"], 20)
        self.assertEqual(store.ops.blocked[-1]["reason"], "ATOMIC_SOCCER_ALLOWANCE_EXHAUSTED")

    def test_new_provider_response_advances_quota_state(self) -> None:
        store = self.store(90, 10)
        store.record_quota(
            ApiResponse(
                data={},
                status=200,
                request_url="https://example.test/sports",
                quota_remaining=89,
                quota_used=11,
                quota_last=1,
            ),
            operation="sports_all",
            observed_at="2026-08-14T04:00:00Z",
        )
        self.assertEqual(store.ops.quota_state["remaining"], 89)
        self.assertEqual(store.ops.quota_state["used"], 11)
        self.assertEqual(len(store.ops.observations), 1)


if __name__ == "__main__":
    unittest.main()
