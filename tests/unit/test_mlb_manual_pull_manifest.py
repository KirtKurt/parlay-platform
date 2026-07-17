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
