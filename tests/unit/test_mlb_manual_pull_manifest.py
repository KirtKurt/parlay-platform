from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_manual_pull
import inqsi_pull_history
import mlb_daily_lock_coverage_patch
import mlb_daily_pick_lock


class ConditionalCollision(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
        super().__init__("conditional collision")


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ConditionalCollision()
        self.items[key] = copy.deepcopy(Item)
        return {}

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item is not None else {}


def canonical_games():
    return [
        {
            "game_id": "provider-with-odds",
            "id": "provider-with-odds",
            "game_key": "mlb|2026-07-16|away one|home one",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
            "provider_sport_key": "baseball_mlb",
            "books": {"fanduel": {"ml": {"home": -120, "away": 110}}},
        },
        {
            "game_id": "provider-no-odds",
            "id": "provider-no-odds",
            "game_key": "mlb|2026-07-16|away two|home two",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Two",
            "away_team": "Away Two",
            "provider_sport_key": "baseball_mlb",
            "books": {},
        },
    ]


def test_provider_game_without_supported_odds_remains_in_slate_manifest():
    compact = mlb_manual_pull._compact([
        {
            "id": "provider-no-odds",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Club",
            "away_team": "Away Club",
            "bookmakers": [],
        }
    ])

    assert compact["count"] == 1
    assert compact["game_dates_et"] == ["2026-07-16"]
    game = compact["games"][0]
    assert game["game_id"] == "provider-no-odds"
    assert game["books"] == {}
    assert game["odds_available"] is False
    assert game["moneyline_available"] is False
    assert game["markets_stored"] == []


def test_quota_free_event_roster_exact_id_merge_keeps_games_missing_from_odds():
    events = [
        {
            "id": "event-with-odds",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
        },
        {
            "id": "event-without-odds",
            "commence_time": "2026-07-16T23:00:00+00:00",
            "home_team": "Home Two",
            "away_team": "Away Two",
        },
    ]
    odds_games = [
        {
            **events[0],
            "bookmakers": [
                {
                    "key": "fanduel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home One", "price": -120},
                                {"name": "Away One", "price": 110},
                            ],
                        }
                    ],
                }
            ],
        },
        {
            # Same teams are not sufficient: an odds-only id must not attach
            # to, or expand, the authoritative event roster.
            "id": "odds-only-different-id",
            "commence_time": events[1]["commence_time"],
            "home_team": events[1]["home_team"],
            "away_team": events[1]["away_team"],
            "bookmakers": [],
        },
    ]

    merged = mlb_manual_pull._merge_event_roster_with_odds(events, odds_games)
    compact = mlb_manual_pull._compact(merged)

    assert [game["id"] for game in merged] == [
        "event-with-odds",
        "event-without-odds",
    ]
    assert compact["count"] == 2
    by_id = {game["game_id"]: game for game in compact["games"]}
    assert by_id["event-with-odds"]["moneyline_available"] is True
    assert by_id["event-without-odds"]["books"] == {}
    assert by_id["event-without-odds"]["moneyline_available"] is False
    assert compact["provider_roster"] == {
        "source": "the_odds_api_events_exact_id_merge",
        "eventRosterCount": 2,
        "oddsPayloadCount": 1,
        "eventsWithoutOddsCount": 1,
        "oddsOnlyCount": 0,
        "exactProviderIdMerge": True,
        "quotaChargedForRosterRequest": False,
    }


def test_fetch_uses_events_endpoint_and_timestamps_only_after_both_responses(monkeypatch):
    calls = []
    events = [
        {
            "id": "event-1",
            "commence_time": "2026-07-16T22:00:00+00:00",
            "home_team": "Home One",
            "away_team": "Away One",
        }
    ]

    def fake_get(url, timeout=20):
        calls.append(url)
        if "/events?" in url:
            assert timeout == 10
        return events if "/events?" in url else []

    monkeypatch.setattr(mlb_manual_pull, "ODDS_API_KEY", "test-key")
    monkeypatch.setattr(mlb_manual_pull, "_http_get_json", fake_get)
    monkeypatch.setattr(mlb_manual_pull, "_now_iso", lambda: calls.append("timestamp") or "2026-07-16T20:00:00+00:00")

    merged, asof = mlb_manual_pull._fetch_odds_with_completion_timestamp()

    assert "/odds/?" in calls[0]
    assert "/events?" in calls[1]
    assert calls[2] == "timestamp"
    assert asof == "2026-07-16T20:00:00+00:00"
    assert [row["id"] for row in merged] == ["event-1"]


def test_event_roster_merge_treats_provider_ids_as_opaque_and_fails_on_empty_roster():
    event = {
        "id": " event-1 ",
        "commence_time": "2026-07-16T22:00:00+00:00",
        "home_team": "Home One",
        "away_team": "Away One",
    }
    odds = [{**event, "id": "event-1", "bookmakers": [{"key": "fanduel", "markets": []}]}]

    merged = mlb_manual_pull._merge_event_roster_with_odds([event], odds)

    assert merged[0]["id"] == " event-1 "
    assert merged[0]["bookmakers"] == []
    with pytest.raises(RuntimeError, match="EVENTS_ROSTER_EMPTY_WHILE_ODDS_NONEMPTY"):
        mlb_manual_pull._merge_event_roster_with_odds([], odds)


def test_manual_pull_canonical_history_keeps_provider_game_without_odds():
    compact = {"games": canonical_games()}

    games = mlb_manual_pull._canonical_games(compact)

    assert [game["game_id"] for game in games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert games[1]["books"] == {}
    assert games[1]["odds_available"] is False


def test_normalize_pull_builds_full_schedule_manifest_without_filtering_empty_books():
    normalized = inqsi_pull_history.normalize_pull({
        "pull_id": "pull-full-manifest",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T17:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })

    assert normalized["ok"] is True
    pull = normalized["pull"]
    assert len(pull["games"]) == 2
    assert pull["games"][1]["books"] == {}
    manifest = pull["provider_schedule_manifest"]
    binding = pull["provider_manifest_binding"]
    assert manifest["version"] == inqsi_pull_history.PROVIDER_MANIFEST_VERSION
    assert manifest["gameCount"] == 2
    assert manifest["gameIdentities"] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert all("books" not in game for game in manifest["games"])
    assert manifest["fingerprint"] == inqsi_pull_history.provider_manifest_fingerprint(manifest)
    assert binding["fingerprint"] == manifest["fingerprint"]
    assert binding["immutable"] is True
    assert binding["fullProviderSchedule"] is True
    assert inqsi_pull_history.validate_provider_schedule_manifest(pull, "2026-07-16") == []


def test_store_pull_persists_independent_write_once_manifest_and_lock_reads_it(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    body = {
        "pull_id": "pull-full-manifest",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T17:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    }

    stored = inqsi_pull_history.store_pull(body)
    repeated = inqsi_pull_history.store_pull(body)

    assert stored["ok"] is True
    authority = stored["stored"]["provider_manifest"]
    assert authority["immutable"] is True
    assert authority["full_provider_schedule"] is True
    assert authority["game_count"] == 2
    assert authority["created"] is True
    assert repeated["stored"]["provider_manifest"]["created"] is False
    pull = stored["pull"]
    assert inqsi_pull_history.validate_provider_schedule_manifest(
        pull,
        "2026-07-16",
        verify_immutable_storage=True,
    ) == []

    lock_games = mlb_daily_pick_lock._latest_games_for_date("2026-07-16", [pull])
    assert [game["game_id"] for game in lock_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    # The scoreable game may be enriched from pull history, but the schedule
    # authority must still retain the provider-visible no-odds game.
    assert not (lock_games[1].get("books") or {})

    manifest_key = (authority["pk"], authority["sk"])
    table.items[manifest_key]["data"]["games"][0]["home_team"] = "Tampered Club"
    with pytest.raises(RuntimeError, match="MLB_PROVIDER_SCHEDULE_MANIFEST_INVALID"):
        mlb_daily_pick_lock._latest_games_for_date("2026-07-16", [pull])


def test_verified_full_slate_manifest_survives_post_start_feed_contraction(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    full = inqsi_pull_history.store_pull({
        "pull_id": "full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T20:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    contracted = inqsi_pull_history.store_pull({
        "pull_id": "contracted-after-game-one-start",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T22:30:00+00:00",
        "source": "the_odds_api",
        "games": [canonical_games()[1]],
    })["pull"]

    resolved = inqsi_pull_history.verified_full_slate_manifest(
        [full, contracted],
        "2026-07-16",
    )
    lock_games = mlb_daily_pick_lock._latest_games_for_date(
        "2026-07-16",
        [full, contracted],
    )
    history_calls = []

    class History:
        @staticmethod
        def verified_full_slate_manifest(pulls, slate):
            history_calls.append((pulls, slate))
            return inqsi_pull_history.verified_full_slate_manifest(pulls, slate)

    class Module:
        history = History()
        _game_date_et = staticmethod(mlb_daily_pick_lock._game_date_et)
        _parse_dt = staticmethod(mlb_daily_pick_lock._parse_dt)

    coverage_games = mlb_daily_lock_coverage_patch._latest_games(
        Module(),
        "2026-07-16",
        [full, contracted],
    )

    assert resolved["latestFeedContracted"] is True
    assert resolved["fullSlateGameCount"] == 2
    assert resolved["latestFeedGameCount"] == 1
    assert resolved["fullAuthorityPullId"] == "full-prestart"
    assert resolved["latestFeedPullId"] == "contracted-after-game-one-start"
    assert [game["game_id"] for game in lock_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert [game["game_id"] for game in coverage_games] == [
        "provider-with-odds",
        "provider-no-odds",
    ]
    assert len(history_calls) == 1


def test_verified_full_slate_manifest_rejects_future_game_omission(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(inqsi_pull_history, "PULLS", table)
    full = inqsi_pull_history.store_pull({
        "pull_id": "full-prestart",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T20:00:00+00:00",
        "source": "the_odds_api",
        "games": canonical_games(),
    })["pull"]
    invalid_subset = inqsi_pull_history.store_pull({
        "pull_id": "premature-subset",
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "pulled_at": "2026-07-16T21:00:00+00:00",
        "source": "the_odds_api",
        "games": [canonical_games()[0]],
    })["pull"]

    with pytest.raises(RuntimeError, match="MLB_PROVIDER_LATEST_FEED_FUTURE_GAME_OMITTED"):
        inqsi_pull_history.verified_full_slate_manifest(
            [full, invalid_subset],
            "2026-07-16",
        )
