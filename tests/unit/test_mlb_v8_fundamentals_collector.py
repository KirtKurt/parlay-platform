from datetime import datetime, timezone

from hello_world.mlb_v8_fundamentals_collector import build_snapshot, normalize_match


def _complete_match():
    lineup = [
        {"id": f"p{i}", "name": f"Player {i}", "battingOrder": i, "position": "OF", "ops": 0.750}
        for i in range(1, 10)
    ]
    return {
        "id": "game-1",
        "date": "2026-07-29",
        "awayTeam": {"id": "A", "name": "Away", "record": "55-50"},
        "homeTeam": {"id": "H", "name": "Home", "record": "60-45"},
        "awayStarter": {"id": "asp", "name": "Away SP", "confirmed": True, "stats": {"era": 3.2, "fip": 3.4}},
        "homeStarter": {"id": "hsp", "name": "Home SP", "confirmed": True, "stats": {"era": 3.0, "fip": 3.1}},
        "awayBullpen": {"era": 3.5, "freshnessScore": 0.8},
        "homeBullpen": {"era": 3.4, "freshnessScore": 0.9},
        "awayLineup": {"confirmed": True, "players": lineup},
        "homeLineup": {"confirmed": True, "players": lineup},
        "awayInjuries": [],
        "homeInjuries": [{"playerId": "x", "name": "Player X", "status": "IL", "impact": "starter"}],
    }


def test_complete_match_is_training_eligible():
    game = normalize_match(_complete_match(), datetime(2026, 7, 29, tzinfo=timezone.utc))
    assert game["trainingEligible"] is True
    assert game["coverage"]["missingDomains"] == []
    assert game["lineups"]["away"]["playerCount"] == 9
    assert game["pitchers"]["home"]["fip"] == 3.1


def test_unconfirmed_or_missing_fundamentals_fail_closed():
    raw = _complete_match()
    raw["homeLineup"]["confirmed"] = False
    raw.pop("awayBullpen")
    game = normalize_match(raw, datetime(2026, 7, 29, tzinfo=timezone.utc))
    assert game["trainingEligible"] is False
    assert "bullpens" in game["coverage"]["missingDomains"]
    assert game["coverage"]["confirmedLineups"] is False


def test_snapshot_is_v8_only_and_does_not_change_v7():
    snapshot = build_snapshot(
        [_complete_match()],
        "2026-07-29",
        datetime(2026, 7, 29, tzinfo=timezone.utc),
        "abc123",
    )
    assert snapshot["authority"] == "V8_FUNDAMENTALS_SHADOW_ONLY"
    assert snapshot["productionV7Unchanged"] is True
    assert snapshot["automaticWagerAllowed"] is False
    assert snapshot["trainingEligibleGameCount"] == 1
    assert len(snapshot["fingerprint"]) == 64
