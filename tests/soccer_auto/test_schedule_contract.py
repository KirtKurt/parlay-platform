from __future__ import annotations

import unittest

from soccer_auto.schedule import (
    collection_status,
    daily_collection_windows,
    match_day_for,
    stabilize_daily_collection_windows,
)


class DailyWindowTests(unittest.TestCase):
    def test_all_games_open_ten_hours_before_first_game(self) -> None:
        events = [
            {"commence_time": "2026-08-14T14:00:00Z"},
            {"commence_time": "2026-08-14T20:00:00Z"},
        ]
        windows = daily_collection_windows(events, timezone_name="America/New_York")
        window = windows["2026-08-14"]
        self.assertEqual(window.first_kickoff, "2026-08-14T14:00:00Z")
        self.assertEqual(window.opens_at, "2026-08-14T04:00:00Z")
        self.assertEqual(window.event_count, 2)
        self.assertFalse(
            collection_status(
                events[1], windows, observed_at="2026-08-14T03:59:59.999Z",
                timezone_name="America/New_York",
            )["open"]
        )
        self.assertTrue(
            collection_status(
                events[1], windows, observed_at="2026-08-14T04:00:00Z",
                timezone_name="America/New_York",
            )["open"]
        )

    def test_local_match_day_handles_midnight_utc(self) -> None:
        self.assertEqual(match_day_for("2026-08-15T03:30:00Z", "America/New_York"), "2026-08-14")
        self.assertEqual(match_day_for("2026-08-15T04:30:00Z", "America/New_York"), "2026-08-15")

    def test_dst_uses_absolute_ten_hour_lead(self) -> None:
        events = [{"commence_time": "2026-03-08T13:00:00Z"}]
        window = daily_collection_windows(events, timezone_name="America/New_York")["2026-03-08"]
        self.assertEqual(window.opens_at, "2026-03-08T03:00:00Z")

    def test_adjacent_days_have_independent_earliest_kickoffs(self) -> None:
        events = [
            {"commence_time": "2026-08-14T14:00:00Z"},
            {"commence_time": "2026-08-15T14:00:00Z"},
        ]
        windows = daily_collection_windows(events, timezone_name="America/New_York")
        self.assertEqual(set(windows), {"2026-08-14", "2026-08-15"})
        self.assertNotEqual(windows["2026-08-14"].opens_at, windows["2026-08-15"].opens_at)

    def test_late_discovered_earlier_game_opens_immediately_without_retroactive_call(self) -> None:
        later = {"commence_time": "2026-08-14T20:00:00Z"}
        original = daily_collection_windows([later], timezone_name="America/New_York")
        self.assertFalse(
            collection_status(
                later,
                original,
                observed_at="2026-08-14T08:00:00Z",
                timezone_name="America/New_York",
            )["open"]
        )
        earlier = {"commence_time": "2026-08-14T14:00:00Z"}
        revised = daily_collection_windows([earlier, later], timezone_name="America/New_York")
        status = collection_status(
            later,
            revised,
            observed_at="2026-08-14T08:00:00Z",
            timezone_name="America/New_York",
        )
        self.assertTrue(status["open"])
        self.assertEqual(status["opens_at"], "2026-08-14T04:00:00Z")

    def test_completed_first_game_cannot_move_an_open_boundary_later(self) -> None:
        later_game_only = daily_collection_windows(
            [{"commence_time": "2026-08-15T00:30:00Z"}],
            timezone_name="America/New_York",
        )
        stable = stabilize_daily_collection_windows(
            later_game_only,
            [
                {
                    "PK": "COLLECTION_WINDOW",
                    "SK": "2026-08-14",
                    "match_day": "2026-08-14",
                    "timezone": "America/New_York",
                    "first_kickoff": "2026-08-14T04:30:00Z",
                    "scheduled_open_at": "2026-08-13T18:30:00Z",
                    "event_count": 2,
                }
            ],
        )
        window = stable["2026-08-14"]
        self.assertEqual(window.first_kickoff, "2026-08-14T04:30:00Z")
        self.assertEqual(window.opens_at, "2026-08-13T18:30:00Z")
        self.assertEqual(window.event_count, 2)

    def test_newly_discovered_earlier_game_can_move_a_boundary_earlier(self) -> None:
        current = daily_collection_windows(
            [
                {"commence_time": "2026-08-14T14:00:00Z"},
                {"commence_time": "2026-08-14T20:00:00Z"},
            ],
            timezone_name="America/New_York",
        )
        stable = stabilize_daily_collection_windows(
            current,
            [
                {
                    "match_day": "2026-08-14",
                    "timezone": "America/New_York",
                    "first_kickoff": "2026-08-14T20:00:00Z",
                    "scheduled_open_at": "2026-08-14T10:00:00Z",
                    "event_count": 1,
                }
            ],
        )
        self.assertEqual(stable["2026-08-14"].first_kickoff, "2026-08-14T14:00:00Z")
        self.assertEqual(stable["2026-08-14"].opens_at, "2026-08-14T04:00:00Z")


if __name__ == "__main__":
    unittest.main()
