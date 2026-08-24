from datetime import datetime, timezone

from hello_world import mlb_three_api_policy as policy


def _game(game_id: str, start: str, away: str, home: str):
    return {
        "official_game_pk": game_id,
        "official_commence_time": start,
        "away_team": away,
        "home_team": home,
    }


def test_deadlines_protect_first_game_and_complete_card_before_second_game():
    games = [
        _game("1", "2026-08-24T17:00:00+00:00", "Away 1", "Home 1"),
        _game("2", "2026-08-24T18:00:00+00:00", "Away 2", "Home 2"),
        _game("3", "2026-08-24T23:00:00+00:00", "Away 3", "Home 3"),
    ]
    deadlines = policy.card_deadlines(games)
    assert deadlines.first_game_prediction_deadline_utc.isoformat() == "2026-08-24T16:15:00+00:00"
    assert deadlines.complete_card_deadline_utc.isoformat() == "2026-08-24T17:15:00+00:00"
    assert deadlines.per_game_deadline_utc["1"].isoformat() == "2026-08-24T16:15:00+00:00"
    assert deadlines.per_game_deadline_utc["2"].isoformat() == "2026-08-24T17:15:00+00:00"
    assert deadlines.per_game_deadline_utc["3"].isoformat() == "2026-08-24T17:15:00+00:00"


def test_full_card_validation_requires_every_official_game_and_winner_loser():
    games = [
        _game("1", "2026-08-24T17:00:00+00:00", "Away 1", "Home 1"),
        _game("2", "2026-08-24T18:00:00+00:00", "Away 2", "Home 2"),
    ]
    picks = [
        {
            "official_game_pk": "1",
            "predictedWinner": "Home 1",
            "predictedLoser": "Away 1",
            "lockedAtUtc": "2026-08-24T16:10:00+00:00",
        },
        {
            "official_game_pk": "2",
            "predictedWinner": "Away 2",
            "predictedLoser": "Home 2",
            "lockedAtUtc": "2026-08-24T17:10:00+00:00",
        },
    ]
    result = policy.validate_daily_card(
        games,
        picks,
        card_published_at_utc="2026-08-24T17:10:00+00:00",
    )
    assert result["ok"] is True
    assert result["allGamesPredicted"] is True
    assert result["completeCardTimely"] is True


def test_daily_accuracy_uses_full_official_slate_denominator():
    rows = [
        {"correct": True},
        {"correct": True},
        {"correct": True},
        {"correct": True},
        {"correct": True},
        {"correct": True},
        {"correct": True},
        {"correct": False},
        {"correct": False},
        {"correct": False},
    ]
    result = policy.daily_accuracy(rows, official_game_count=10)
    assert result["dailyAccuracy"] == 0.70
    assert result["goalMet"] is True
    assert result["completeOfficialSlateDenominator"] is True
