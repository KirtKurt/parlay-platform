from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

import mlb_statsapi_team_context as source

NOW = datetime(2026, 9, 9, 14, tzinfo=timezone.utc)
PASSIVE_KEYS = (
    "bullpenRosterObservationStatus",
    "bullpenRosterSourceProvenance",
    "home_bullpen_roster_player_ids",
    "away_bullpen_roster_player_ids",
)


@pytest.fixture(autouse=True)
def clear_cache():
    source._CACHE.clear()
    yield
    source._CACHE.clear()


def _current_feed_fixture():
    game = {
        "gamePk": 123,
        "gameDate": "2026-09-09T20:00:00Z",
        "status": {"abstractGameState": "Preview"},
        "teams": {
            "home": {"team": {"id": 1}},
            "away": {"team": {"id": 2}},
        },
    }
    teams = {
        "home": {
            "team": {"id": 1},
            "bullpen": [111, 112],
            "players": {
                "ID111": {"person": {"id": 111}},
                "ID112": {"person": {"id": 112}},
            },
        },
        "away": {
            "team": {"id": 2},
            "bullpen": [211, 212],
            "players": {
                "ID211": {"person": {"id": 211}},
                "ID212": {"person": {"id": 212}},
            },
        },
    }
    feed = {
        "gameData": {
            "game": {"pk": 123},
            "datetime": {"dateTime": game["gameDate"]},
            "status": {"abstractGameState": "Preview"},
        },
        "liveData": {"boxscore": {"teams": teams}},
    }
    history_unavailable = {
        "ok": False,
        "payload": {"totalGames": 0, "dates": []},
    }
    return game, feed, history_unavailable


def _observe(game, feed, history):
    def get(url, timeout):
        assert timeout == 4
        assert url.endswith("/feed/live")
        return feed

    return source.observe("2026-09-09", game, history, get, now=lambda: NOW)


def test_valid_current_rosters_survive_unavailable_workload_history_without_gaining_authority():
    import mlb_three_api_llm_analyst as analyst

    game, feed, history = _current_feed_fixture()
    _lineup, bullpen = _observe(game, feed, history)

    assert bullpen["source_status"] == "NOT_CONNECTED_SOURCE_REQUIRED"
    assert bullpen["bullpenRosterObservationStatus"] == "OBSERVED_ROSTER_ONLY"
    assert bullpen["home_bullpen_roster_player_ids"] == [111, 112]
    assert bullpen["away_bullpen_roster_player_ids"] == [211, 212]
    assert "home_reliever_usage_1d_3d_5d" not in bullpen
    assert "away_reliever_usage_1d_3d_5d" not in bullpen

    stripped = deepcopy(bullpen)
    for key in PASSIVE_KEYS:
        stripped.pop(key, None)
    game_for_analyst = {"officialGamePk": 123, "homeTeam": "Home", "awayTeam": "Away"}
    observed = analyst.build_evidence(
        game_for_analyst, {"bullpen_fatigue": bullpen}, as_of_utc=NOW.isoformat()
    )
    baseline = analyst.build_evidence(
        game_for_analyst, {"bullpen_fatigue": stripped}, as_of_utc=NOW.isoformat()
    )
    assert observed == baseline


@pytest.mark.parametrize(
    "mutate",
    [
        lambda feed: feed["gameData"]["game"].__setitem__("pk", 123.0),
        lambda feed: feed["liveData"]["boxscore"]["teams"]["home"]["team"].__setitem__("id", True),
        lambda feed: feed["liveData"]["boxscore"]["teams"]["away"]["team"].__setitem__("id", 2.0),
    ],
)
def test_malformed_top_level_feed_identities_never_retain_roster_observations(mutate):
    game, feed, history = _current_feed_fixture()
    mutate(feed)
    _lineup, bullpen = _observe(game, feed, history)
    for key in PASSIVE_KEYS:
        assert key not in bullpen
