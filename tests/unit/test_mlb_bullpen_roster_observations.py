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


def _team(roster=None):
    roster = [111, 112] if roster is None else roster
    return {
        "bullpen": roster,
        "players": {
            "ID111": {"person": {"id": 111}},
            "ID112": {"person": {"id": 112}},
        },
    }


def test_bullpen_roster_requires_unique_strict_identity_bound_player_ids():
    assert source._bullpen_roster(_team()) == [111, 112]
    for invalid in ([], [111, 111], [True, 112], [0, 112], ["111", 112]):
        assert source._bullpen_roster(_team(invalid)) is None

    malformed = [
        {"bullpen": [111], "players": []},
        {"bullpen": [111], "players": {"ID111": []}},
        {"bullpen": [111], "players": {"ID111": {"person": []}}},
        {"bullpen": [111], "players": {"ID111": {"person": {"id": True}}}},
        {"bullpen": [111], "players": {"ID111": {"person": {"id": "111"}}}},
    ]
    for team in malformed:
        assert source._bullpen_roster(team) is None

    wrong = _team()
    wrong["players"]["ID111"]["person"]["id"] = 999
    assert source._bullpen_roster(wrong) is None


def _fixtures():
    game = {
        "gamePk": 123,
        "gameDate": "2026-09-09T20:00:00Z",
        "status": {"abstractGameState": "Preview"},
        "teams": {
            "home": {"team": {"id": 1}},
            "away": {"team": {"id": 2}},
        },
    }
    teams = {}
    for side, base in (("home", 100), ("away", 200)):
        order = list(range(base + 1, base + 10))
        players = {
            "ID" + str(identity): {
                "person": {"id": identity},
                "battingOrder": str(slot * 100),
                "gameStatus": {"isSubstitute": False},
                "seasonStats": {"batting": {"ops": ".800", "plateAppearances": 100}},
            }
            for slot, identity in enumerate(order, 1)
        }
        for identity, started in ((base + 10, 1), (base + 11, 0), (base + 12, 0)):
            players["ID" + str(identity)] = {
                "person": {"id": identity},
                "stats": {
                    "pitching": {
                        "gamesStarted": started,
                        "numberOfPitches": 30 if started else 18,
                        "outs": 9 if started else 3,
                    }
                },
            }
        teams[side] = {
            "team": game["teams"][side]["team"],
            "battingOrder": order,
            "players": players,
            "pitchers": [base + 10, base + 11, base + 12],
            "bullpen": [base + 11, base + 12],
        }
    feed = {
        "gameData": {
            "game": {"pk": 123},
            "datetime": {"dateTime": game["gameDate"]},
            "status": game["status"],
        },
        "liveData": {"boxscore": {"teams": teams}},
    }
    historical_teams = deepcopy(teams)
    old = deepcopy(game)
    old.update(
        gamePk=122,
        gameDate="2026-09-08T20:00:00Z",
        status={"abstractGameState": "Final"},
    )
    history = {
        "ok": True,
        "historyStartDateEt": "2026-09-04",
        "historyEndDateEt": "2026-09-09",
        "endpoint": "https://statsapi.mlb.com/api/v1/schedule",
        "retrievedAtUtc": NOW.isoformat(),
        "payloadFingerprint": "a" * 64,
        "payload": {"totalGames": 1, "dates": [{"date": "2026-09-08", "games": [old]}]},
    }

    def get(url, timeout):
        assert timeout == 4
        return feed if url.endswith("/feed/live") else {"teams": historical_teams}

    return game, history, feed, teams, historical_teams, get


def test_observe_retains_current_roster_as_observation_not_availability():
    game, history, feed, teams, historical_teams, get = _fixtures()
    _, bullpen = source.observe("2026-09-09", game, history, get, now=lambda: NOW)
    assert bullpen["source_status"] == "PARTIAL"
    assert bullpen["bullpenRosterObservationStatus"] == "OBSERVED_ROSTER_ONLY"
    assert bullpen["home_bullpen_roster_player_ids"] == [111, 112]
    assert bullpen["away_bullpen_roster_player_ids"] == [211, 212]
    assert bullpen["bullpenRosterSourceProvenance"]["retrievedAtUtc"] == NOW.isoformat()
    assert "available" not in " ".join(bullpen).lower()
    assert "availability" in bullpen["note"].lower()
    assert bullpen["home_reliever_usage_1d_3d_5d"]["3d"] == {"pitches": 36, "outs": 6}


def test_malformed_current_roster_is_omitted_without_poisoning_lineup_or_workload():
    game, history, feed, teams, historical_teams, get = _fixtures()
    teams["home"]["players"]["ID111"]["person"] = []
    lineup, bullpen = source.observe("2026-09-09", game, history, get, now=lambda: NOW)
    assert lineup["source_status"] == "CONNECTED"
    assert bullpen["source_status"] == "PARTIAL"
    assert bullpen["home_reliever_usage_1d_3d_5d"]["1d"] == {"pitches": 36, "outs": 6}
    for key in PASSIVE_KEYS:
        assert key not in bullpen


def test_roster_observation_does_not_complete_bullpen_group_or_change_strict_features():
    import mlb_fundamentals_snapshot_v2 as snapshots
    import mlb_ml_dual_model_v2 as r8

    game, history, feed, teams, historical_teams, get = _fixtures()
    lineup, bullpen = source.observe("2026-09-09", game, history, get, now=lambda: NOW)
    row = {
        "gameId": "mlb_statsapi:123",
        "officialGamePk": 123,
        "slateDateEt": "2026-09-09",
        "predictionSourcePullAt": NOW.isoformat(),
        "advanced_context": {"confirmed_lineups": lineup, "bullpen_fatigue": bullpen},
    }
    without_roster = deepcopy(row)
    for key in PASSIVE_KEYS:
        without_roster["advanced_context"]["bullpen_fatigue"].pop(key, None)

    observed = snapshots.build(row, captured_at_utc=NOW.isoformat())
    baseline = snapshots.build(without_roster, captured_at_utc=NOW.isoformat())
    assert observed["groups"]["bullpen_availability"] == baseline["groups"]["bullpen_availability"]
    assert "bullpen_availability" in observed["missingGroups"]
    assert not observed["groups"]["bullpen_availability"]["complete"]
    assert r8._strict_features({"fundamentalsSnapshotV2": observed}, {}) == r8._strict_features(
        {"fundamentalsSnapshotV2": baseline}, {}
    )


def test_passive_roster_metadata_is_absent_from_authority_bearing_analyst_evidence():
    import mlb_three_api_llm_analyst as analyst

    game, history, feed, teams, historical_teams, get = _fixtures()
    lineup, bullpen = source.observe("2026-09-09", game, history, get, now=lambda: NOW)
    with_roster = {"confirmed_lineups": lineup, "bullpen_fatigue": bullpen}
    without_roster = deepcopy(with_roster)
    for key in PASSIVE_KEYS:
        without_roster["bullpen_fatigue"].pop(key, None)

    analyst_game = {
        "officialGamePk": 123,
        "homeTeam": "Home",
        "awayTeam": "Away",
    }
    observed = analyst.build_evidence(analyst_game, with_roster, as_of_utc=NOW.isoformat())
    baseline = analyst.build_evidence(analyst_game, without_roster, as_of_utc=NOW.isoformat())
    assert observed == baseline
    bullpen_evidence = observed["bigBallsDataPro"]["bullpen"]
    assert all(key not in bullpen_evidence for key in PASSIVE_KEYS)
