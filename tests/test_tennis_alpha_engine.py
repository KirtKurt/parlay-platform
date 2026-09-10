from engine import TennisEngine, backtest


def _match(date, winner, loser, surface="Hard", wpts=2000, lpts=800, best_of="3"):
    return {
        "tourney_date": date,
        "winner_name": winner,
        "loser_name": loser,
        "surface": surface,
        "winner_rank_points": wpts,
        "loser_rank_points": lpts,
        "best_of": best_of,
    }


def test_stronger_player_gets_higher_p_after_history():
    eng = TennisEngine("atp")
    for i in range(25):
        eng.observe(_match(20200101 + i, "Ace", "Pushed"))
    p_fav = eng.predict_row(_match(20210101, "Ace", "Pushed"), "Ace", "Pushed")
    p_dog = eng.predict_row(_match(20210101, "Ace", "Pushed"), "Pushed", "Ace")
    assert p_fav > 0.55
    assert p_fav > p_dog


def test_temporal_split_does_not_train_on_test():
    rows = [_match(20240101 + i, "A", "B") for i in range(10)]
    rows += [_match(20260101 + i, "A", "B") for i in range(5)]
    result = backtest(rows, "atp", 20250101)
    assert result["test_matches"] == 5
    assert result["training_samples"] == 10
    assert result["accuracy"] >= 0.5
