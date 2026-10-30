"""Ops loop: registry, backlog, walk-forward, failure taxonomy."""
from __future__ import annotations

from stack2mlb.backlog import next_item
from stack2mlb.experiments import lookup
from stack2mlb.failures import classify, summarize
from stack2mlb.ops import report
from stack2mlb.walkforward import evaluate


def test_rejected_second_gbdt_stays_rejected():
    exp = lookup("EXP-2026-09-11-A")
    assert exp is not None
    assert exp.promotion_decision == "reject"


def test_shadow_experiment_not_promoted():
    exp = lookup("EXP-2026-09-11-B")
    assert exp.promotion_decision == "shadow_only"
    assert exp.sample_size == 2


def test_next_item_is_walkforward_ledger():
    item = next_item()
    assert item.item_id == "BL-01"


def test_walkforward_is_chronological_and_unpromoted():
    games = []
    for i in range(12):
        fight = i % 3 == 0
        games.append({
            "game_id": str(i),
            "commence_time": f"2026-08-{10+i:02d}T23:00:00+00:00",
            "p_lgb": 0.77 if fight else 0.58,
            "p_poisson": 0.52 if fight else 0.57,
            "p_market": 0.55 if fight else 0.56,
            "p_elo": 0.54 if fight else 0.57,
            "home_win": 1 if i % 2 == 0 else 0,
            "home_id": "147",
            "away_id": "121",
        })
    out = evaluate(games)
    assert out["games"] == 12
    assert out["promoted"] is False
    assert out["promotion_allowed"] is False
    assert out["statuses"]["bet"] < 12


def test_failure_taxonomy_does_not_treat_one_loss_as_a_retrain():
    rows = [
        {"home_win": 0, "p_lgb": 0.77, "p_poisson": 0.54, "p_market": 0.59},
        {"home_win": 1, "p_lgb": 0.52, "p_poisson": 0.51, "p_market": 0.50},
        {"home_win": 0, "p_lgb": 0.51, "p_poisson": 0.50, "p_market": 0.50},
    ]
    assert classify(rows[0]) == "engine_disagreement"
    assert classify(rows[2]) == "normal_randomness_coin_flip"
    assert "single miss" in summarize(rows)["rule"].lower()


def test_ops_report_names_champion_and_bottleneck():
    rec = report()
    assert rec["champion_model"].startswith("KS1")
    assert rec["promoted"] is False
    assert rec["current_biggest_bottleneck"]["item_id"] == "BL-01"
