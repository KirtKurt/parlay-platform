import mlb_daily_per_game_lock_patch as per_game_lock


def test_candidate_snapshot_aliases_query_only_persisted_identities():
    game = {
        "game_id": "root",
        "officialGamePk": "822865",
        "commence_time": "2026-08-05T17:00:00+00:00",
        "away_team": "New York Yankees",
        "home_team": "Texas Rangers",
    }
    scoring = [
        {
            "games": [
                {
                    "game_id": f"alias-{index}",
                    "officialGamePk": "822865",
                    "commence_time": "2026-08-05T17:00:00+00:00",
                    "away_team": "New York Yankees",
                    "home_team": "Texas Rangers",
                }
            ]
        }
        for index in range(3)
    ]

    aliases = per_game_lock._candidate_snapshot_aliases(game, scoring)

    assert aliases == ["alias-0", "alias-1", "alias-2", "root"]
    assert "mlb_statsapi:822865" not in aliases
