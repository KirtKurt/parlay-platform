from ks1.starter_identity import published_starter_index


def test_published_index_keeps_latest_pregame_and_drops_post_t10():
    rows = [
        {"game_id": "3", "as_of": "2026-08-03T19:20:00Z", "commence_time": "2026-08-03T20:00:00Z",
         "home_starter_id": "11", "home_starter_name": "Early", "p_home": 0.91},
        {"game_id": "3", "as_of": "2026-08-03T19:40:00Z", "commence_time": "2026-08-03T20:00:00Z",
         "home_starter_id": "99", "home_starter_name": "Observed starter",
         "away_starter_id": "99", "away_starter_name": "Observed starter", "p_home": 0.12},
        {"game_id": "3", "as_of": "2026-08-03T20:01:00Z", "commence_time": "2026-08-03T20:00:00Z",
         "home_starter_id": "77", "home_starter_name": "Too late"},
    ]
    index = published_starter_index(rows)
    assert index["3"]["home"]["id"] == "99"
    assert index["3"]["away"]["id"] == "99"
    assert index["3"]["as_of"] == "2026-08-03T19:40:00Z"
    assert "p_home" not in index["3"]


def test_published_index_ignores_blank_and_missing_timing():
    assert published_starter_index([
        {"game_id": "1", "home_starter_id": "99"},
        {"game_id": "2", "as_of": "2026-08-03T19:40:00Z", "commence_time": "2026-08-03T20:00:00Z",
         "home_starter_id": None},
    ]) == {}
