from hello_world.ml_winner_engine import slate_expected_miss_layer


def test_expected_misses_are_probability_sum_and_not_quota():
    predictions = [
        {"prediction_status": "PUBLISHED", "predicted_team": "A", "game_key": "g1", "confidence": 80, "raw_features": {}},
        {"prediction_status": "PUBLISHED", "predicted_team": "B", "game_key": "g2", "confidence": 70, "raw_features": {"late_reversal": True}},
        {"prediction_status": "PUBLISHED", "predicted_team": "C", "game_key": "g3", "confidence": 60, "raw_features": {"spread_disagreement": True}},
    ]
    result = slate_expected_miss_layer(predictions, "nhl")
    assert result["expected_misses"] == 0.9
    assert result["likely_miss_count_band"]["center"] == 1
    assert result["most_vulnerable_picks"][0]["game_key"] in {"g2", "g3"}
    assert "Never flip picks" in result["guardrail"]


def test_non_published_rows_do_not_enter_slate_expectation():
    result = slate_expected_miss_layer([
        {"prediction_status": "WATCHLIST", "predicted_team": "A", "confidence": 90},
        {"prediction_status": "NO_EDGE", "predicted_team": None, "confidence": 50},
    ], "nhl")
    assert result["published_pick_count"] == 0
    assert result["expected_misses"] == 0.0
