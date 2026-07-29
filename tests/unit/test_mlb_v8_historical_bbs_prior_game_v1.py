from __future__ import annotations

from hello_world import mlb_v8_historical_bbs_prior_game_v1 as prior


def row(day, home, away, home_runs, away_runs, *, status="finished", match_id=None):
    return {
        "id": match_id or f"{day}-{home}-{away}",
        "kickoff_utc": f"{day}T23:00:00Z",
        "status": status,
        "home": {"name": home},
        "away": {"name": away},
        "score": {"home": home_runs, "away": away_runs},
    }


def test_score_pair_supports_unified_nested_shape():
    value = {
        "scores": {"value": {"home": 5, "away": 3}},
    }
    assert prior.score_pair(value) == (5.0, 3.0)


def test_nonfinal_game_is_not_admitted_to_history():
    assert prior.completed_game(
        row(
            "2026-07-01",
            "New York Yankees",
            "Boston Red Sox",
            5,
            3,
            status="scheduled",
        )
    ) is None


def test_same_day_results_are_excluded_from_target_features():
    rows = [
        row("2026-07-01", "New York Yankees", "Boston Red Sox", 5, 3),
        row("2026-07-02", "New York Yankees", "Boston Red Sox", 4, 2),
        row("2026-07-03", "New York Yankees", "Boston Red Sox", 6, 1),
        row("2026-07-04", "New York Yankees", "Boston Red Sox", 2, 1),
        row("2026-07-05", "New York Yankees", "Boston Red Sox", 3, 1),
        row("2026-07-06", "Boston Red Sox", "New York Yankees", 9, 0),
    ]
    ledger = prior.build_team_ledger(rows)
    features = prior.derive_game_features(
        ledger,
        {
            "slateDateEt": "2026-07-06",
            "homeTeam": "New York Yankees",
            "awayTeam": "Boston Red Sox",
            "homeWon": 0,
        },
    )

    assert features["trainingEligible"] is True
    assert features["sameDayResultsExcluded"] is True
    assert features["targetGameOutcomeUsed"] is False
    assert features["home"]["bbsWinRate5"] == 1.0
    assert features["away"]["bbsWinRate5"] == 0.0


def test_prior_game_floor_fails_closed():
    ledger = prior.build_team_ledger(
        [row("2026-07-01", "New York Yankees", "Boston Red Sox", 5, 3)]
    )
    features = prior.derive_game_features(
        ledger,
        {
            "slateDateEt": "2026-07-02",
            "homeTeam": "New York Yankees",
            "awayTeam": "Boston Red Sox",
        },
    )

    assert features["trainingEligible"] is False
    assert "home_bbs_prior_game_floor_not_met" in features["eligibilityErrors"]
    assert "away_bbs_prior_game_floor_not_met" in features["eligibilityErrors"]


def test_duplicate_provider_game_is_counted_once():
    game = row(
        "2026-07-01",
        "New York Yankees",
        "Boston Red Sox",
        5,
        3,
        match_id="same",
    )
    ledger = prior.build_team_ledger([game, dict(game)])
    assert len(ledger["new york yankees"]) == 1
    assert len(ledger["boston red sox"]) == 1
