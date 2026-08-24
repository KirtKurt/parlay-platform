from __future__ import annotations

import pytest

import ml_authority


def packet():
    return {
        "slateDateEt": "2026-08-24",
        "deadline": {"publishDeadlineUtc": "2026-08-24T21:55:00+00:00"},
        "sourceStatus": {},
        "games": [
            {
                "gamePk": "1",
                "gameDate": "2026-08-24T22:40:00Z",
                "home": {"name": "Home One"},
                "away": {"name": "Away One"},
                "official": {"gamePk": "1"},
                "oddsCore": {"id": "odds-1"},
                "oddsExpanded": {"markets": {"h2h": {}}},
                "bbs": {
                    "match": {"id": "bbs-1"},
                    "statistics": {"ok": True, "data": {"x": 1}},
                    "lineups": {"ok": True, "data": [{"id": "p1"}]},
                },
                "bbsLeagueContext": {},
            },
            {
                "gamePk": "2",
                "gameDate": "2026-08-24T23:00:00Z",
                "home": {"name": "Home Two"},
                "away": {"name": "Away Two"},
                "official": {"gamePk": "2"},
                "oddsCore": {"id": "odds-2"},
                "oddsExpanded": {"markets": {"h2h": {}}},
                "bbs": {"match": {"id": "bbs-2"}},
                "bbsLeagueContext": {},
            },
        ],
    }


def test_ml_card_matches_every_game_and_uses_bbd_calibration(monkeypatch):
    monkeypatch.setattr(
        ml_authority,
        "fetch_predictions",
        lambda slate: {
            "ok": True,
            "model_version": ml_authority.EXPECTED_MODEL,
            "primaryAlgorithm": "INQSI-MLB-RANKED-WINNER-v15.10.0-active-ensemble",
            "winner_predictions": [
                {
                    "gamePk": "1",
                    "homeTeam": "Home One",
                    "awayTeam": "Away One",
                    "predictedWinner": "Home One",
                    "probability": 0.61,
                },
                {
                    "gamePk": "2",
                    "homeTeam": "Home Two",
                    "awayTeam": "Away Two",
                    "predictedWinner": "Away Two",
                    "probability": 0.58,
                },
            ],
        },
    )
    card = ml_authority.build_card(packet(), bedrock_failure="quota")
    assert card["gameCount"] == 2
    assert card["mlPickCount"] == 2
    assert card["fallbackPickCount"] == 0
    assert all(
        row["decisionAuthority"] == "AWS_ML_RANKED_ENSEMBLE"
        for row in card["picks"]
    )
    assert card["picks"][0]["bbsContextScore"] > card["picks"][1]["bbsContextScore"]
    assert card["picks"][0]["probability"] <= card["picks"][0]["baseModelProbability"]


def test_ml_card_fails_closed_when_a_game_is_missing(monkeypatch):
    monkeypatch.setattr(
        ml_authority,
        "fetch_predictions",
        lambda slate: {
            "ok": True,
            "winner_predictions": [
                {
                    "gamePk": "1",
                    "predictedWinner": "Home One",
                    "probability": 0.61,
                }
            ],
        },
    )
    with pytest.raises(RuntimeError, match="MLB_ML_GAME_COVERAGE_INCOMPLETE"):
        ml_authority.build_card(packet())
