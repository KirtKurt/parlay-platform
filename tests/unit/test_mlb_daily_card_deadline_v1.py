from datetime import datetime, timezone

from hello_world import mlb_daily_card_deadline_v1 as policy


def _games():
    return [
        {"official_game_pk": "1", "official_commence_time": "2026-08-24T17:00:00+00:00"},
        {"official_game_pk": "2", "official_commence_time": "2026-08-24T18:10:00+00:00"},
        {"official_game_pk": "3", "official_commence_time": "2026-08-24T19:00:00+00:00"},
    ]


def test_first_pick_and_full_card_deadlines_are_t45():
    result = policy.compute_deadlines(_games(), slate_date="2026-08-24", lead_minutes=45)
    assert result.first_pick_deadline_utc.isoformat() == "2026-08-24T16:15:00+00:00"
    assert result.full_card_deadline_utc.isoformat() == "2026-08-24T17:25:00+00:00"
    assert result.second_game_id == "2"


def test_one_game_slate_uses_only_game_as_full_card_anchor():
    result = policy.compute_deadlines(_games()[:1], slate_date="2026-08-24")
    assert result.first_pick_deadline_utc == result.full_card_deadline_utc


def test_publication_audit_requires_all_games_and_pregame_evidence():
    predictions = [
        {
            **game,
            "predictedWinner": "Home",
            "predictedAtUtc": "2026-08-24T16:10:00+00:00",
        }
        for game in _games()
    ]
    audit = policy.publication_audit(
        _games(),
        predictions,
        slate_date="2026-08-24",
        published_at="2026-08-24T16:10:00+00:00",
    )
    assert audit["timingHealthy"] is True
    assert audit["fullCardDeadlineMet"] is True
    assert audit["allGamesPredicted"] is True
