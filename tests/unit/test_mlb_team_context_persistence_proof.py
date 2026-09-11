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
        "lineupSeasonBattingVersion": SUBJECT.source.BATTING_OBSERVATION_VERSION,
        "sourceProvenance": {
            "provider": "MLB Stats API",
            "dataset": SUBJECT.source.VERSION,
            "retrievedAtUtc": "2026-09-11T18:00:00+00:00",
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
        "bullpenRosterObservationStatus": "OBSERVED_ROSTER_ONLY",
        "bullpenRosterSourceProvenance": {
            "provider": "MLB Stats API",
            "dataset": SUBJECT.source.VERSION,
            "retrievedAtUtc": "2026-09-11T18:00:00+00:00",
        },
        "home_bullpen_roster_player_ids": [Decimal(301), Decimal(302)],
        "away_bullpen_roster_player_ids": [Decimal(401), Decimal(402)],
    }
    return {
        "PK": "GAME_WINNERS#mlb#2026-09-11",
        "SK": "GAME#fixture",
        "data": {
            "officialGamePk": Decimal(824631),
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


def test_passive_bullpen_roster_never_satisfies_available_relievers():
    row = _row()
    row["data"]["advanced_context"]["bullpen_fatigue"]["home_available_relievers"] = [301]
    with pytest.raises(RuntimeError, match="passive_roster_must_not_claim_available_relievers"):
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
