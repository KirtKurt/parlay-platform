from elo import EloBook, replay


def test_favorite_moves_little_on_expected_win():
    book = EloBook()
    book.overall["A"] = 1800
    book.overall["B"] = 1400
    before_a, before_b = book.overall["A"], book.overall["B"]
    book.update("A", "B", "hard", "20260101")
    assert book.overall["A"] > before_a
    assert book.overall["B"] < before_b
    assert (book.overall["A"] - before_a) < 8.0


def test_replay_separates_surfaces():
    book = replay(
        [
            {"winner_name": "A", "loser_name": "B", "surface": "Clay", "tourney_date": "1"},
            {"winner_name": "A", "loser_name": "B", "surface": "Clay", "tourney_date": "2"},
            {"winner_name": "B", "loser_name": "A", "surface": "Grass", "tourney_date": "3"},
        ]
    )
    _, a_clay = book.get("A", "clay")
    _, a_grass = book.get("A", "grass")
    assert a_clay > 1500
    assert a_grass < 1500
