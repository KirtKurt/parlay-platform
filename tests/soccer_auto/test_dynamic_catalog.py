from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.soccer_auto.aws_stubs import install_if_needed

install_if_needed()

from soccer_auto.collector import (  # noqa: E402
    _discover_sport,
    _fetch_outrights,
    catalog_handler,
    inventory_handler,
)
from soccer_auto.odds_api import ApiResponse  # noqa: E402


class CatalogStore:
    def __init__(self):
        self.competitions = []
        self.events = []
        self.jobs = []
        self.claims = []
        self.released = []

    def record_quota(self, *args, **kwargs):
        pass

    def archive_json(self, *args, **kwargs):
        return "s3://soccer/raw.json", "digest"

    def put_competition(self, row, observed_at):
        self.competitions.append(row)

    def put_event(self, row, observed_at):
        self.events.append(row)

    def enqueue(self, job):
        self.jobs.append(job)

    def list_competitions(self, active_only=False):
        return [
            {"sport_key": "soccer_future_league", "active": True, "has_outrights": False}
        ]

    def claim_job(self, claim, expires_at):
        self.claims.append(claim)
        return True

    def release_job(self, claim):
        self.released.append(claim)


class CatalogClient:
    def sports(self, include_inactive=True):
        return ApiResponse(
            data=[
                {"key": "soccer_future_league", "group": "Soccer", "active": True},
                {"key": "soccer_prefixed_future", "group": "Future", "active": True},
                {"key": "basketball_nba", "group": "Basketball", "active": True},
            ],
            status=200,
            request_url="https://example.test/sports",
        )

    def events(self, sport_key):
        return ApiResponse(
            data=[
                {
                    "id": "event-id",
                    "sport_key": sport_key,
                    "commence_time": "2026-08-14T14:00:00Z",
                    "home_team": "Home",
                    "away_team": "Away",
                }
            ],
            status=200,
            request_url="https://example.test/events",
        )

    def odds(self, sport_key, markets, *, regions):
        return ApiResponse(
            data=[
                {
                    "id": "tournament-winner",
                    "sport_key": sport_key,
                    "commence_time": "2026-09-01T00:00:00Z",
                    "bookmakers": [],
                }
            ],
            status=200,
            request_url="https://example.test/outrights",
        )


class DynamicCatalogTests(unittest.TestCase):
    def test_new_soccer_keys_are_admitted_without_static_code_change(self) -> None:
        store = CatalogStore()
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector._client", return_value=CatalogClient()
        ), patch("soccer_auto.collector._observed_at", return_value="2026-08-14T00:00:00Z"):
            result = catalog_handler({}, None)
        self.assertEqual(result["soccer_competitions"], 2)
        self.assertEqual(
            {row["key"] for row in store.competitions},
            {"soccer_future_league", "soccer_prefixed_future"},
        )

    def test_outright_competition_still_discovers_game_events(self) -> None:
        store = CatalogStore()
        result = _discover_sport(
            store,
            CatalogClient(),
            {
                "sport_key": "soccer_tournament",
                "has_outrights": True,
                "collect_outrights": False,
            },
        )
        self.assertEqual(result["events_stored"], 1)
        self.assertEqual(result["outrights_enqueued"], 0)
        self.assertEqual(len(store.events), 1)

    def test_inventory_refreshes_events_without_paid_work_queue(self) -> None:
        store = CatalogStore()
        with patch("soccer_auto.collector.SoccerStore", return_value=store), patch(
            "soccer_auto.collector._client", return_value=CatalogClient()
        ):
            result = inventory_handler({}, None)
        self.assertTrue(result["queue_bypassed"])
        self.assertEqual(result["competitions_refreshed"], 1)
        self.assertEqual(len(store.events), 1)
        self.assertEqual(store.jobs, [])

    def test_outright_snapshot_never_becomes_a_game_schedule_event(self) -> None:
        class OutrightStore:
            def __init__(self):
                self.manifests = []

            def provider_budget_available(self, *args, **kwargs):
                return True

            def record_quota(self, *args, **kwargs):
                pass

            def archive_json(self, *args, **kwargs):
                return "s3://soccer/outrights.json", "outright-digest"

            def put_outright_manifest(self, **kwargs):
                self.manifests.append(kwargs)

        store = OutrightStore()
        result = _fetch_outrights(
            store,
            CatalogClient(),
            {"sport_key": "soccer_fifa_world_cup_winner"},
        )
        self.assertEqual(result["events"], 1)
        self.assertFalse(result["schedule_planner_eligible"])
        self.assertEqual(store.manifests[0]["event_count"], 1)


if __name__ == "__main__":
    unittest.main()
