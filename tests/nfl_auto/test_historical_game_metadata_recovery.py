from __future__ import annotations

from datetime import date

import pytest

from scripts.nfl_recover_historical_game_metadata import (
    augment_bbd_row,
    match_schedule_event,
    safe_schedule_anchors,
)
from nfl_auto.features import parse_bbd_game


def bbd_row(**overrides):
    row = {
        "season": 2025,
        "week": 1,
        "game_type": "REG",
        "game_date": "2025-09-07",
        "game_id": "2025_01_ARI_NO",
        "away_team": "ARI",
        "home_team": "NO",
        "away_score": 20,
        "home_score": 24,
        "away_rest": 7,
        "home_rest": 7,
    }
    row.update(overrides)
    return row


def event(**overrides):
    value = {
        "id": "event-1",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2025-09-07T17:00:00Z",
        "away_team": "Arizona Cardinals",
        "home_team": "New Orleans Saints",
    }
    value.update(overrides)
    return value


def test_safe_schedule_anchors_are_pregame_for_nfl_calendar_date():
    anchors = safe_schedule_anchors(date(2025, 9, 7))
    assert anchors == ("2025-09-06T23:55:00Z", "2025-09-07T11:55:00Z")


def test_unique_role_correct_schedule_event_supplies_exact_kickoff():
    row = bbd_row()
    matched = match_schedule_event(row, [event()])
    game = parse_bbd_game(augment_bbd_row(row, matched))
    assert game.kickoff_utc == "2025-09-07T17:00:00Z"
    assert game.home_team == "NO"
    assert game.away_team == "ARI"


def test_la_alias_is_resolved_only_by_unique_opponent_and_role():
    row = bbd_row(
        game_id="2025_01_SF_LA",
        home_team="LA",
        away_team="SF",
    )
    matched = match_schedule_event(
        row,
        [
            event(
                id="rams",
                home_team="Los Angeles Rams",
                away_team="San Francisco 49ers",
            ),
            event(
                id="chargers-other-opponent",
                home_team="Los Angeles Chargers",
                away_team="Kansas City Chiefs",
            ),
        ],
    )
    game = parse_bbd_game(augment_bbd_row(row, matched))
    assert game.home_team == "LAR"
    assert game.away_team == "SF"


def test_la_alias_fails_closed_when_schedule_match_is_ambiguous():
    row = bbd_row(home_team="LA", away_team="SF")
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        match_schedule_event(
            row,
            [
                event(id="a", home_team="Los Angeles Rams", away_team="San Francisco 49ers"),
                event(id="b", home_team="Los Angeles Chargers", away_team="San Francisco 49ers"),
            ],
        )


def test_schedule_event_outside_bbd_calendar_window_is_rejected():
    with pytest.raises(ValueError, match="NOT_FOUND"):
        match_schedule_event(
            bbd_row(),
            [event(commence_time="2025-09-10T17:00:00Z")],
        )


def test_unknown_non_la_team_is_never_guessed():
    with pytest.raises(ValueError, match="NFL_TEAM_UNRECOGNIZED"):
        match_schedule_event(bbd_row(home_team="MYSTERY"), [event()])
