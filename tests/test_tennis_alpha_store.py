from ratings import RatingStore


def test_ratings_round_trip():
    store = RatingStore()
    store.observe("A", "B", "clay", 20240301)
    store.observe("A", "C", "hard", 20240401)
    restored = RatingStore.from_dict(store.to_dict())
    assert restored.matches == 2
    assert restored.pre_match("A", "B", "clay")["elo_diff"] > 0
    assert "A" in restored.names()
