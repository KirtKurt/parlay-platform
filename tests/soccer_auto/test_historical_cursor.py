from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.historical import run_additional  # noqa: E402
from soccer_auto.odds_api import ApiResponse  # noqa: E402


class Ops:
    def __init__(self, cursor):
        self.cursor = dict(cursor)
        self.writes = []

    def get_item(self, **kwargs):
        return {"Item": dict(self.cursor)}

    def put_item(self, **kwargs):
        item = dict(kwargs["Item"])
        self.writes.append(item)
        if item.get("PK") == "HISTORICAL_CURSOR":
            self.cursor = item


class Store:
    def __init__(self, cursor):
        self.ops = Ops(cursor)
        self.budget_checks = 0

    def list_competitions(self):
        return [{"sport_key": "soccer_future_league", "has_outrights": False}]

    def provider_budget_available(self, *args, **kwargs):
        self.budget_checks += 1
        return self.budget_checks == 1

    def record_quota(self, *args, **kwargs):
        pass

    def archive_json(self, *args, **kwargs):
        return "s3://raw/item.json", "payload-hash"


class Client:
    def historical_event_odds(self, *args, **kwargs):
        return ApiResponse(
            data={"timestamp": "2026-01-01T00:00:00Z", "data": []},
            status=200,
            request_url="https://example.test/history",
        )


class HistoricalCursorTests(unittest.TestCase):
    def test_additional_resume_keeps_market_batch_offset(self) -> None:
        cursor = {
            "PK": "HISTORICAL_CURSOR",
            "SK": "ADDITIONAL",
            "entity_type": "SOCCER_HISTORICAL_BACKFILL_CURSOR",
            "competition_index": 0,
            "snapshot_at": "2026-01-01T00:00:00Z",
            "pending_sport_key": "soccer_future_league",
            "pending_provider_at": "2026-01-01T00:00:00Z",
            "pending_requested_at": "2026-01-01T00:00:00Z",
            "pending_next_timestamp": "2026-01-01T00:05:00Z",
            "pending_events": [{"id": "event-1"}],
            "pending_event_index": 0,
            "pending_market_index": 0,
            "calls_completed": 0,
        }
        store = Store(cursor)
        with patch("soccer_auto.historical._client", return_value=Client()), patch(
            "soccer_auto.historical._market_keys_for_sport",
            return_value=["market_a", "market_b", "market_c"],
        ):
            result = run_additional(store)
        self.assertTrue(result["deferred"])
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["cursor"]["pending_event_index"], 0)
        self.assertEqual(result["cursor"]["pending_market_index"], 1)
        self.assertEqual(result["cursor"]["calls_completed"], 1)


if __name__ == "__main__":
    unittest.main()
