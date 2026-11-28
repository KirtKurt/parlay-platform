"""2stackMLB contract tests. Official p_home is immutable."""
from __future__ import annotations

from stack2mlb import DISAGREE_PP, VERSION
from stack2mlb.bayes_market import update as bayes_update
from stack2mlb.calibrate import reliability, wilson_lower
from stack2mlb.elo import EloBook, expected
from stack2mlb.market import american_to_implied, fair_from_american
from stack2mlb.markov import p_home as markov_p_home, simulate
from stack2mlb.poisson_bridge import home_probability, residual_features
from stack2mlb.slate import tonight
from stack2mlb.stack import decide


def test_version():
    assert VERSION.startswith("2stackMLB")


def test_devig_nyy_line():
    fair = fair_from_american(-142, 122)
    assert 0.56 < fair < 0.61
    raw = american_to_implied(-142)
    assert raw > fair


def test_elo_refuses_blowout_overfit():
    book = EloBook()
    before = book.p_home("147", "115")
    book.update("147", "115", True, margin=7)
    after = book.p_home("147", "115")
    assert after > before
    assert after - before < 0.03


def test_elo_expected_is_standard():
    assert abs(expected(0) - 0.5) < 1e-12
    assert expected(400) > 0.90


def test_poisson_symmetric():
    p, tie = home_probability(4.5, 4.5)
    assert abs(p - 0.5) < 1e-6
    assert 0.05 < tie < 0.16


def test_poisson_favors_higher_lambda():
    p, _ = home_probability(5.2, 3.8)
    assert p > 0.60


def test_residual_features_include_gaps():
    feats = residual_features(4.55, 3.95, market_home=0.587, p_lgb=0.77)
    assert feats["lgb_minus_poisson"] > 0.10
    assert feats["poisson_minus_market"] != 0


def test_bayes_caps_the_seventy_seven():
    capped = bayes_update(0.587, 0.77, kappa=0.25)
    assert capped < 0.587 + DISAGREE_PP + 1e-9
    assert capped < 0.70


def test_markov_tracks_poisson_direction():
    p_even = markov_p_home(4.4, 4.4, sims=4000, seed=1)
    p_home_fav = markov_p_home(5.4, 3.6, sims=4000, seed=1)
    assert abs(p_even - 0.5) < 0.04
    assert p_home_fav > 0.62


def test_markov_extra_innings_exist():
    result = simulate(4.4, 4.4, sims=5000, seed=2)
    assert 0.05 < result.extra_inning_rate < 0.20


def test_official_p_home_never_rewritten():
    d = decide(p_lgb=0.77, p_poisson=0.54, p_market=0.587, p_elo=0.58, p_home_official=0.77)
    assert d.p_home_official == 0.77
    assert d.p_stack != d.p_lgb
    assert d.pick_status in {"pass", "shrink"}


def test_lgb_loses_when_structural_engines_agree():
    d = decide(p_lgb=0.77, p_poisson=0.46, p_market=0.48, p_elo=0.47, p_home_official=0.77)
    assert d.lgb_is_outlier is True
    assert d.pick_status != "bet"
    assert d.agreed_side == "away"


def test_bet_only_on_true_agreement():
    d = decide(p_lgb=0.58, p_poisson=0.57, p_market=0.56, p_elo=0.57, p_home_official=0.58)
    assert d.pick_status == "bet"
    assert d.agreed_side == "home"
    assert d.max_engine_gap <= DISAGREE_PP


def test_starter_gate():
    d = decide(
        p_lgb=0.58, p_poisson=0.57, p_market=0.56, p_elo=0.57,
        p_home_official=0.58, starter_unverified=True,
    )
    assert d.pick_status == "pass"
    assert d.selection_reason == "starter_unverified"


def test_tonight_sits_both_problem_games():
    slate = tonight(markov_sims=2500)
    by_id = {row["game_id"]: row for row in slate}
    nyy = by_id["2026-09-11-NYM@NYY"]
    cws = by_id["2026-09-11-CWS@STL"]
    assert nyy["pick_status"] != "bet"
    assert cws["pick_status"] != "bet"
    assert nyy["p_home_official"] == 0.77
    assert abs(cws["p_lgb"] - (1.0 - 0.64)) < 1e-12
    assert nyy["max_engine_gap"] > DISAGREE_PP


def test_reliability_buckets_cover_unit_interval():
    y = [0, 1, 1, 0, 1, 0, 1, 1, 0, 1]
    p = [0.1, 0.2, 0.3, 0.4, 0.55, 0.6, 0.7, 0.8, 0.9, 0.95]
    rows = reliability(y, p, bins=10)
    assert len(rows) == 10
    assert sum(r["count"] for r in rows) == 10


def test_wilson_requires_real_sample():
    assert wilson_lower(9, 9) < 0.80
    assert wilson_lower(70, 100) > 0.60
