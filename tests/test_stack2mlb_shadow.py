"""Shadow attach + Elo-from-grades. Official SCHEMA is not this package's to change."""
from __future__ import annotations

from pathlib import Path

from stack2mlb.elo_history import book_from_grades
from stack2mlb.live import attach, starter_unverified
from stack2mlb.shadow import SHADOW_PREFIX, attach_slate, chart, write_sidecar


def _row(**overrides):
    row = {
        "date": "2026-09-11",
        "game_id": "776001",
        "home_id": "147",
        "away_id": "121",
        "home_team": "NYY",
        "away_team": "NYM",
        "p_home": 0.77,
        "p_home_poisson": 0.54,
        "lambda_home": 4.55,
        "lambda_away": 3.95,
        "market_home_prob": 0.587,
        "home_starter_id": "rodon",
        "away_starter_id": "mclean",
        "lineup_status": "confirmed",
        "starter_feature_source": "individual",
        "home_starter_status": "confirmed",
        "away_starter_status": "confirmed",
    }
    row.update(overrides)
    return row


def test_live_attach_does_not_rewrite_official():
    out = attach(_row(), markov_sims=1500)
    assert out["p_home_official"] == 0.77
    assert out["pick_status"] != "bet"
    assert out["shadow"] is True
    assert out["promoted"] is False


def test_missing_market_is_pass():
    out = attach(_row(market_home_prob=None), markov_sims=500)
    assert out["pick_status"] == "pass"
    assert out["p_home_official"] == 0.77


def test_probable_starter_gates():
    assert starter_unverified(_row(lineup_status="projected", starter_feature_source="team_starter_prior"))
    out = attach(_row(lineup_status="projected", starter_feature_source="team_starter_prior"), markov_sims=500)
    assert out["pick_status"] == "pass"
    assert out["selection_reason"] == "starter_unverified"


def test_elo_book_uses_only_prior_grades():
    grades = [
        {"game_id": "1", "home_id": "147", "away_id": "115", "home_win": 1,
         "home_score": 10, "away_score": 3, "commence_time": "2026-09-10T23:05:00+00:00",
         "lock_row": {"home_id": "147", "away_id": "115", "home_starter_id": "fried",
                      "away_starter_id": "feltner", "commence_time": "2026-09-10T23:05:00+00:00"}},
        {"game_id": "2", "home_id": "147", "away_id": "121", "home_win": 1,
         "home_score": 4, "away_score": 3, "commence_time": "2026-09-11T23:05:00+00:00",
         "lock_row": {"home_id": "147", "away_id": "121", "commence_time": "2026-09-11T23:05:00+00:00"}},
    ]
    before = book_from_grades(grades, before="2026-09-11T00:00:00+00:00")
    full = book_from_grades(grades)
    assert getattr(before, "_graded") == 1
    assert getattr(full, "_graded") == 2
    assert before.p_home("147", "121") != full.p_home("147", "121")


def test_sidecar_writes_shadow_prefix_only(tmp_path: Path):
    report = write_sidecar([_row(), _row(game_id="776002", p_home=0.36, p_home_poisson=0.56,
                                         market_home_prob=0.496, home_id="138", away_id="145")],
                           tmp_path)
    assert report["promoted"] is False
    assert report["bet"] == 0
    payload = (tmp_path / "stack2mlb_shadow.json").read_text()
    assert SHADOW_PREFIX in payload
    assert "predictions-v1" in payload


def test_chart_forbids_promotion():
    grades = [{"game_id": "776001", "home_win": 1}]
    shadows = attach_slate([_row()], grades=[], markov_sims=800)
    report = chart(grades, shadows)
    assert report["promoted"] is False
    assert report["promotion_allowed"] is False
    assert report["games"] == 1
