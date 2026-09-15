from soccer_auto.kss1_markets import apply_abstain, markets_from_grid, net_edges, score_matrix


def _markets():
    return markets_from_grid(score_matrix(1.6, 1.1))


def test_devig_and_positive_edge_keeps_1x2():
    markets = _markets()
    books = {"1x2": {"home": 0.40, "draw": 0.30, "away": 0.30}}
    out = apply_abstain(markets, books=books, require_positive_edge=True, min_1x2=0.40, min_double_chance=0.67 + 1e-9)
    assert out["edge_1x2"] is not None
    if out["edge_1x2"] >= 0.02:
        assert out["1x2_published"] == markets["1x2_pick"]
    else:
        assert out["1x2_published"] == "ABSTAIN"


def test_negative_edge_abstains_only_that_book():
    markets = {
        "1x2_pick": "home", "1x2_probability": 0.50,
        "p_home": 0.50, "p_draw": 0.25, "p_away": 0.25,
        "p_1x": 0.75, "p_12": 0.75, "p_x2": 0.50,
        "double_chance_pick": "1X", "double_chance_probability": 0.75,
        "p_over_25": 0.60, "p_under_25": 0.40, "ou25_pick": "over",
        "p_btts_yes": 0.60, "p_btts_no": 0.40, "btts_pick": "yes",
    }
    books = {
        "1x2": {"home": 0.62, "draw": 0.20, "away": 0.18},
        "ou25": {"over": 0.40, "under": 0.60},
        "btts": {"yes": 0.40, "no": 0.60},
        "dc": {"1X": 0.80, "12": 0.10, "X2": 0.10},
    }
    out = apply_abstain(markets, books=books, require_positive_edge=True, min_1x2=0.40, min_other=0.51, min_double_chance=0.70)
    assert out["1x2_published"] == "ABSTAIN"
    assert out["double_chance_published"] == "ABSTAIN"
    assert out["ou25_published"] == "over"
    assert out["btts_published"] == "yes"


def test_missing_books_do_not_force_abstain():
    markets = _markets()
    edges = net_edges(markets, {})
    assert edges["edge_1x2"] is None
    out = apply_abstain(markets, books=None, require_positive_edge=True, min_1x2=0.40)
    assert out["1x2_published"] == markets["1x2_pick"]
