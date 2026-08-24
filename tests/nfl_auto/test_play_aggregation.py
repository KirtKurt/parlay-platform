from nfl_auto.features import Game, aggregate_game_plays, pregame_team_features


def game(game_id: str, kickoff: str) -> Game:
    return Game(
        game_id=game_id,
        season=2024,
        week=1,
        game_type="REG",
        kickoff_utc=kickoff,
        home_team="BUF",
        away_team="MIA",
        home_score=24,
        away_score=17,
        home_rest=7,
        away_rest=7,
    )


def test_play_aggregation_and_pregame_no_leakage() -> None:
    first = game("2024_01_MIA_BUF", "2024-09-01T17:00:00Z")
    second = game("2024_02_MIA_BUF", "2024-09-08T17:00:00Z")
    plays = [
        {"posteam": "BUF", "defteam": "MIA", "play_type": "pass", "down": 1, "yards_gained": 25, "epa": 1.2},
        {"posteam": "BUF", "defteam": "MIA", "play_type": "run", "down": 3, "yards_gained": 5, "epa": 0.4, "success": 1},
        {"posteam": "MIA", "defteam": "BUF", "play_type": "pass", "down": 1, "yards_gained": 2, "epa": -0.5, "interception": 1},
        {"posteam": "MIA", "defteam": "BUF", "play_type": "run", "down": 3, "yards_gained": 1, "epa": -0.2, "success": 0},
    ]
    stats = aggregate_game_plays(first, plays)
    assert stats["BUF"].offensive_epa > stats["MIA"].offensive_epa
    pregame = pregame_team_features([first, second], {first.game_id: stats})
    assert pregame[first.game_id]["BUF"]["games_available"] == 0
    assert pregame[second.game_id]["BUF"]["games_available"] == 1
    assert pregame[second.game_id]["BUF"]["offensive_epa"] == stats["BUF"].offensive_epa
