import ast
from pathlib import Path


def test_handler_parses():
    ast.parse(Path("tennis_learning/handler.py").read_text())


def test_expected_features_present():
    text = Path("tennis_learning/handler.py").read_text()
    for name in (
        "market_fair_prob",
        "surface_elo_diff_scaled",
        "recent_win_rate_diff",
        "serve_points_won_diff",
        "return_points_won_diff",
    ):
        assert name in text


def test_durable_and_idempotent_contracts_present():
    text = Path("tennis_learning/handler.py").read_text()
    assert "transact_write_items" in text
    assert "attribute_not_exists(PK)" in text
    assert "TENNIS_LEARNING_TABLE" in text
