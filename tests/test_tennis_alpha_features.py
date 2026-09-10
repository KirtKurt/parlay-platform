from features import FEATURE_NAMES, build
from model import initial_state, predict_probability, sgd_step


def test_favorite_has_higher_market_prob():
    feat = build({"player_odds": -200, "opponent_odds": 170})
    assert feat["market_fair_prob"] > 0.6
    assert set(feat) == set(FEATURE_NAMES)


def test_sgd_moves_toward_label():
    signals = {
        "player_odds": -150,
        "opponent_odds": 130,
        "elo_diff": 200,
        "surface_elo_diff": 180,
        "rank_points_edge": 800,
        "recent_surface_wr_diff": 0.2,
        "h2h_edge": 0.1,
        "serve_points_won_diff": 0.05,
        "return_points_won_diff": 0.04,
        "break_points_saved_diff": 0.03,
        "rest_days_diff": 1,
        "best_of_five": False,
    }
    state = initial_state()
    p0, _ = predict_probability(state, signals)
    for _ in range(40):
        state = sgd_step(state, signals, True)
    p1, _ = predict_probability(state, signals)
    assert p1 > p0
