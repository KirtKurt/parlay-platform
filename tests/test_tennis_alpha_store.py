from ratings import RatingStore, replay


def test_ratings_round_trip():
    store = RatingStore()
    store.observe("A", "B", "clay", 20240301, winner_pts=1800, loser_pts=900)
    store.observe("A", "C", "hard", 20240401, winner_pts=1900, loser_pts=700)
    restored = RatingStore.from_dict(store.to_dict())
    assert restored.matches == 2
    assert restored.pre_match("A", "B", "clay")["elo_diff"] > 0
    assert restored.rank_points["A"] == 1900
    assert restored.pre_match("A", "B", "clay")["rank_points_edge"] == 1000
    assert "A" in restored.names()


def test_replay_keeps_latest_rank_points():
    store = replay(
        [
            {
                "winner_name": "Ace",
                "loser_name": "Pushed",
                "surface": "Hard",
                "tourney_date": "20240101",
                "winner_rank_points": "1200",
                "loser_rank_points": "400",
            }
        ]
    )
    assert store.rank_points["Ace"] == 1200
    assert store.pre_match("Ace", "Pushed", "hard")["rank_points_edge"] == 800
