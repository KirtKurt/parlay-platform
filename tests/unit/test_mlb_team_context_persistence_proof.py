from __future__ import annotations

import copy
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_mlb_team_context",
    ROOT / "scripts" / "verify_mlb_team_context.py",
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

GAME_PK = 824631
FEED = f"https://statsapi.mlb.com/api/v1.1/game/{GAME_PK}/feed/live"


class Table:
    def __init__(self, items):
        self.items = copy.deepcopy(items)
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        assert kwargs.get("ConsistentRead") is True
        return {"Items": copy.deepcopy(self.items)}


def _row():
    lineup = {
        "source_status": "CONNECTED",
        "game_pk": Decimal(GAME_PK),
        "lineupSeasonBattingVersion": SUBJECT.source.BATTING_OBSERVATION_VERSION,
        "sourceProvenance": {
            "provider": "MLB Stats API",
            "dataset": SUBJECT.source.VERSION,
            "retrievedAtUtc": "2026-09-11T18:00:00+00:00",
            "endpoint": FEED,
        },
    }
    for side, base in (("home", 100), ("away", 200)):
        order = [Decimal(base + index) for index in range(1, 10)]
        lineup[side + "_batting_order"] = order
        lineup[side + "_lineup_season_batting"] = [
            {
                "playerId": Decimal(base + index),
                "battingSlot": Decimal(index),
                "plateAppearances": Decimal(100),
                "ops": Decimal("0.800"),
                "obp": Decimal("0.330"),
                "slg": Decimal("0.470"),
            }
            for index in range(1, 10)
        ]
    bullpen = {
        "source_status": "PARTIAL",
        "game_pk": Decimal(GAME_PK),
        "bullpenRosterObservationStatus": "OBSERVED_ROSTER_ONLY",
        "bullpenRosterSourceProvenance": {
            "provider": "MLB Stats API",
            "dataset": SUBJECT.source.VERSION,
            "retrievedAtUtc": "2026-09-11T18:00:00+00:00",
            "endpoint": FEED,
        },
        "home_bullpen_roster_player_ids": [Decimal(301), Decimal(302)],
        "away_bullpen_roster_player_ids": [Decimal(401), Decimal(402)],
    }
    return {
        "PK": "GAME_WINNERS#mlb#2026-09-11",
        "SK": "GAME#fixture",
        "data": {
            "officialGamePk": Decimal(GAME_PK),
            "commenceTime": "2026-09-11T20:00:00+00:00",
            "advanced_context": {
                "confirmed_lineups": lineup,
                "bullpen_fatigue": bullpen,
            },
        },
    }


def test_persisted_passive_observations_accept_decimal_ddb_identities_without_authority_change():
    table = Table([_row()])
    report = SUBJECT.persisted_observations(table, "2026-09-11")
    assert table.calls == 1
    assert report["readOnly"] is True
    assert report["storedGameRows"] == 1
    assert report["gamesWithPassiveBatterObservations"] == 1
    assert report["gamesWithValidPassiveBatterObservations"] == 1
    assert report["gamesWithPassiveBullpenRosters"] == 1
    assert report["gamesWithValidPassiveBullpenRosters"] == 1
    assert report["passiveRosterAvailabilityClaimCount"] == 0
    assert report["rows"][0]["passiveLineupObservation"]["preT45"] is True
    assert report["rows"][0]["passiveBullpenRosterObservation"]["preT45"] is True


def test_passive_batter_order_mismatch_fails_closed():
    row = _row()
    row["data"]["advanced_context"]["confirmed_lineups"]["home_lineup_season_batting"][0]["playerId"] = Decimal(999)
    with pytest.raises(RuntimeError, match="passive_batter_order_mismatch"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_passive_batter_observation_at_or_after_t45_fails_closed():
    row = _row()
    row["data"]["advanced_context"]["confirmed_lineups"]["sourceProvenance"]["retrievedAtUtc"] = "2026-09-11T19:15:00+00:00"
    with pytest.raises(RuntimeError, match="passive_batting_not_pre_t45"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("field,value", [
    ("home_available_relievers", []),
    ("away_available_relievers", [401]),
    ("home_unavailable_relievers", []),
    ("away_unavailable_relievers", [402]),
])
def test_passive_bullpen_roster_never_makes_available_or_unavailable_claim(field, value):
    row = _row()
    row["data"]["advanced_context"]["bullpen_fatigue"][field] = value
    with pytest.raises(RuntimeError, match="passive_roster_must_not_claim_reliever_availability"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_absent_passive_observations_are_reported_as_absent_not_fabricated():
    row = _row()
    row["data"]["advanced_context"] = {}
    report = SUBJECT.persisted_observations(Table([row]), "2026-09-11")
    assert report["storedGameRows"] == 1
    assert report["gamesWithPassiveBatterObservations"] == 0
    assert report["gamesWithValidPassiveBatterObservations"] == 0
    assert report["gamesWithPassiveBullpenRosters"] == 0
    assert report["gamesWithValidPassiveBullpenRosters"] == 0


def test_version_marker_with_unposted_lineups_is_normal_absence():
    row = _row()
    lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["home_lineup_season_batting"] = None
    lineup["away_lineup_season_batting"] = None
    lineup["home_batting_order"] = None
    lineup["away_batting_order"] = None
    state = SUBJECT.passive_lineup_observation(row["data"])
    assert lineup["lineupSeasonBattingVersion"] == SUBJECT.source.BATTING_OBSERVATION_VERSION
    assert state["present"] is False
    assert state["valid"] is False
    assert state["errors"] == []
    report = SUBJECT.persisted_observations(Table([row]), "2026-09-11")
    assert report["gamesWithPassiveBatterObservations"] == 0


@pytest.mark.parametrize("value", [None, "", "not-a-time"])
def test_present_passive_batter_observation_requires_parseable_game_start(value):
    row = _row()
    row["data"]["commenceTime"] = value
    with pytest.raises(RuntimeError, match="passive_batting_commence_time_invalid"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_passive_lineup_must_belong_to_same_exact_official_game_and_feed():
    row = _row()
    lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["game_pk"] = Decimal(GAME_PK + 1)
    lineup["sourceProvenance"]["endpoint"] = f"https://statsapi.mlb.com/api/v1.1/game/{GAME_PK + 1}/feed/live"
    with pytest.raises(RuntimeError, match="passive_batting_(game|endpoint)_identity_mismatch"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


def test_passive_bullpen_roster_must_bind_to_same_exact_game_feed():
    row = _row()
    bullpen = row["data"]["advanced_context"]["bullpen_fatigue"]
    bullpen.pop("game_pk")
    bullpen["bullpenRosterSourceProvenance"]["endpoint"] = f"https://statsapi.mlb.com/api/v1.1/game/{GAME_PK + 1}/feed/live"
    with pytest.raises(RuntimeError, match="bullpen_roster_endpoint_identity_mismatch"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("invalid", ["101", 101.0, Decimal("101.5"), Decimal("9007199254740992.5")])
def test_persisted_player_ids_do_not_accept_string_float_or_fractional_decimal(invalid):
    row = _row()
    lineup = row["data"]["advanced_context"]["confirmed_lineups"]
    lineup["home_batting_order"][0] = invalid
    lineup["home_lineup_season_batting"][0]["playerId"] = invalid
    with pytest.raises(RuntimeError, match="batting_order_identity_invalid|passive_batter_identity_or_slot_invalid"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")


@pytest.mark.parametrize("invalid", ["301", 301.0, Decimal("301.5")])
def test_persisted_bullpen_ids_use_same_strict_identity_contract(invalid):
    row = _row()
    bullpen = row["data"]["advanced_context"]["bullpen_fatigue"]
    bullpen["home_bullpen_roster_player_ids"][0] = invalid
    with pytest.raises(RuntimeError, match="home_bullpen_roster_identity_invalid"):
        SUBJECT.persisted_observations(Table([row]), "2026-09-11")
