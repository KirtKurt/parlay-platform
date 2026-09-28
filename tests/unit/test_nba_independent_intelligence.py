from nba_intelligence.contract import filter_fundamental_features
from nba_intelligence.layer import evaluate_game, explain_pick, slate_report
from nba_intelligence.audit import ablation_compare, rolling_audit
from nba_intelligence.markets import SUPPORTED_MARKETS, evaluate_market
from nba_intelligence import AUTHORITY_CHANGED


def test_market_names_cannot_enter_fundamentals():
    kept = filter_fundamental_features(
        ["net_rating_30d", "market_home_prob", "moneyline_implied", "rest_days", "steam_move"]
    )
    assert kept == ("net_rating_30d", "rest_days")


def test_evaluate_game_records_flip_without_changing_authority():
    row = evaluate_game(
        event_id="nba-1",
        home="NYK",
        away="BOS",
        p_fundamental_home=0.56,
        p_market_aware_home=0.41,
        p_market_home=0.38,
        active_fundamental_signals=("rest", "net_rating"),
        active_market_signals=("consensus_move",),
        collected_signals=("rest", "net_rating", "consensus_move", "injury_rumor"),
    )
    assert row["market_flip"] is True
    assert row["fundamental_selected_side"] == "NYK"
    assert row["final_selected_side"] == "BOS"
    assert row["favorite"] == "BOS"
    assert row["quota_applied"] is False
    assert row["authority_changed"] is False
    assert AUTHORITY_CHANGED is False
    assert "injury_rumor" in row["diagnostic_only_signals"]
    assert row["credible_underdog"]["flagged"] is True
    assert row["credible_underdog"]["auto_selected"] is False
    assert row["vulnerable_favorite"]["flagged"] is True
    assert row["vulnerable_favorite"]["auto_flipped"] is False


def test_all_favorite_slate_is_allowed():
    report = slate_report(
        [
            {
                "event_id": f"g{i}",
                "home": "H",
                "away": "A",
                "p_fundamental_home": 0.62,
                "p_market_aware_home": 0.64,
                "p_market_home": 0.61,
            }
            for i in range(5)
        ]
    )
    assert report["market_aware_favorites"] == 5
    assert report["quota_applied"] is False
    assert report["authority_changed"] is False


def test_explanation_does_not_invent_shap():
    row = evaluate_game(
        event_id="nba-2",
        home="MIL",
        away="CHI",
        p_fundamental_home=0.58,
        p_market_aware_home=0.57,
        p_market_home=0.55,
    )
    expl = explain_pick(row)
    assert expl["shap_available"] is False
    assert expl["market_flip"] == "NO"
    assert expl["lock_status"] == "UNCHANGED"


def test_markets_are_labeled_separately():
    assert "spread" in SUPPORTED_MARKETS
    spread = evaluate_market(
        market="spread",
        event_id="nba-3",
        home="DEN",
        away="LAL",
        p_fundamental_home=0.51,
        p_market_aware_home=0.54,
        p_market_home=0.53,
    )
    assert spread["market"] == "spread"


def test_rolling_audit_and_ablation_do_not_assume_market_helps():
    rows = [
        {
            "favorite_selected": True,
            "hit": True,
            "brier": 0.2,
            "log_loss": 0.5,
            "p_pred": 0.6,
            "p_market": 0.62,
            "market_flip": False,
        },
        {
            "favorite_selected": False,
            "hit": False,
            "brier": 0.3,
            "log_loss": 0.8,
            "p_pred": 0.45,
            "p_market": 0.40,
            "market_flip": True,
            "fundamentals_would_have_won": True,
        },
    ]
    audit = rolling_audit(rows, windows=(7, 30, 100))
    assert audit["favorites"]["count"] == 1
    assert audit["underdogs"]["count"] == 1
    assert audit["quota_applied"] is False
    compared = ablation_compare(rows, rows)
    assert compared["assumed_market_helps"] is False
    assert compared["authority_changed"] is False
