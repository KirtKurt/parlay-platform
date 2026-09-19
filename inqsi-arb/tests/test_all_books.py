import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import app
import validation  # noqa: F401
from app import lambda_handler
from arb_engine import scan_market
from provider_books import catalog_summary
from rules import COMPATIBLE, INCOMPATIBLE, UNKNOWN, compatibility, lookup
from settlement_matrix import SettlementProofError, prove_quoted_market


def test_catalog_lists_every_us_odds_api_book():
    summary = catalog_summary()
    keys = {row["key"] for row in summary["books"]}
    for key in (
        "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics",
        "espnbet", "betrivers", "hardrockbet", "ballybet", "betparx",
        "bovada", "pinnacle", "prizepicks", "kalshi",
    ):
        assert key in keys
    assert summary["n_us_licensed"] >= 14
    assert all(row["on_math_board"] is True for row in summary["books"])
    assert summary["complete"] is False
    assert summary["catalog_scope"] == "static_metadata_overlay"
    assert summary["all_provider_books_claimed"] is False
    rows = {row["key"]: row for row in summary["books"]}
    for key in ("betanysports", "courtside", "rebet"):
        assert rows[key]["kind"] == "provider_only"
    assert summary["n_provider_only"] == 3


def test_catalog_aggregates_state_scoped_review_coverage():
    rows = {row["key"]: row for row in catalog_summary()["books"]}
    assert rows["fanduel"]["settlement_reviewed"] is True
    assert "nj" in rows["fanduel"]["reviewed_jurisdictions"]
    assert rows["fanatics"]["reviewed_families"]
    assert rows["betmgm"]["reviewed_families"]


def test_catalog_import_bootstraps_supplemental_rule_modules():
    assert "rules_bootstrap" in sys.modules
    assert "rules_fanatics" in sys.modules
    assert "rules_betmgm" in sys.modules
    assert "rules_caesars" in sys.modules
    assert "rules_state_packs" in sys.modules


def test_books_http_route():
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/books"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["places_bets"] is False
    assert body["count"] >= 30
    assert any(row["key"] == "williamhill_us" for row in body["books"])


def test_collector_status_route():
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/collector"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["schedule"] == "rate(2 minutes)"
    assert body["places_bets"] is False


def test_collector_status_survives_checkpoint_dependency_failure(monkeypatch):
    def fail():
        raise RuntimeError("ddb unavailable")
    monkeypatch.setattr(app, "get_checkpoint", fail)
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/collector"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["ok"] is True
    assert body["checkpoint"] is None
    assert body["checkpoint_error"] == "RuntimeError"


def test_betmgm_nj_baseball_unknown_resume_window_stays_distinct():
    rule = lookup("betmgm", "baseball", "winner", "nj")
    assert rule is not None
    assert rule.settlement_profile == "betmgm_nj_mlb_full_game_2way_resume_unverified_v1"
    result = compatibility(["draftkings", "betmgm"], "baseball", "winner", "nj")
    assert result["status"] == INCOMPATIBLE
    assert result["reason"] == "SETTLEMENT_PROFILE_CONFLICT"


def test_betmgm_mi_36h_baseball_does_not_cross_qualify_draftkings():
    result = compatibility(["draftkings", "betmgm"], "baseball", "winner", "mi")
    assert result["status"] == INCOMPATIBLE
    assert result["reason"] == "SETTLEMENT_PROFILE_CONFLICT"


def test_betmgm_unread_state_fails_closed():
    result = compatibility(["draftkings", "betmgm"], "baseball", "winner", "co")
    assert result["status"] == UNKNOWN
    assert "betmgm" in result["missing_books"]


def test_caesars_national_baseball_requires_nine_innings_and_stays_distinct():
    rule = lookup("williamhill_us", "baseball", "winner", "ny")
    assert rule is not None and rule.reviewed is True
    assert rule.settlement_profile == "caesars_mlb_full_game_9_or_8.5_v1"
    result = compatibility(["draftkings", "williamhill_us"], "baseball", "winner", "ny")
    assert result["status"] == INCOMPATIBLE


def test_fanduel_co_nj_pa_baseball_verify_against_draftkings():
    for state in ("co", "nj", "pa"):
        result = compatibility(["draftkings", "fanduel"], "baseball", "winner", state)
        assert result["status"] == COMPATIBLE, state


def test_fanatics_arizona_is_reviewed_and_not_leaked_to_unread_states():
    assert lookup("fanatics", "baseball", "winner", "az") is not None
    assert lookup("fanatics", "baseball", "winner", "co") is None
    result = compatibility(["fanatics", "draftkings"], "baseball", "winner", "az")
    assert result["status"] == INCOMPATIBLE


def test_integer_spread_push_is_not_a_strict_surebet():
    row = scan_market(
        market_id="spread", event="A @ B", market="spreads", bankroll=1000,
        expected_outcomes=["A +3", "B -3"], rules_status="compatible",
        quotes=[
            {"outcome": "A +3", "book": "draftkings", "decimal": 2.2, "point": 3},
            {"outcome": "B -3", "book": "fanduel", "decimal": 2.2, "point": -3},
        ],
    )
    assert row and row["math_arb"] is True
    assert row["arb"] is False
    assert row["validation"]["settlement_states"]["includes_push"] is True
    assert row["validation"]["qualification_reason"] == "SETTLEMENT_STATE_NOT_STRICT"


def test_half_point_spread_has_no_push_state_and_can_verify():
    row = scan_market(
        market_id="spread", event="A @ B", market="spreads", bankroll=1000,
        expected_outcomes=["A +1.5", "B -1.5"], rules_status="compatible",
        quotes=[
            {"outcome": "A +1.5", "book": "draftkings", "decimal": 2.2, "point": 1.5},
            {"outcome": "B -1.5", "book": "fanduel", "decimal": 2.2, "point": -1.5},
        ],
    )
    assert row and row["arb"] is True
    assert row["validation"]["settlement_states"]["includes_push"] is False
    assert row["validation"]["settlement_states"]["strict_arbitrage"] is True


def test_line_market_without_finite_points_fails_closed():
    for point in (None, "nan", "not-a-number"):
        quotes = [
            {"outcome": "A", "book": "draftkings", "decimal": 2.2},
            {"outcome": "B", "book": "fanduel", "decimal": 2.2},
        ]
        if point is not None:
            quotes[0]["point"] = point
            quotes[1]["point"] = point
        row = scan_market(
            market_id="bad-line", event="A @ B", market="spreads", bankroll=1000,
            expected_outcomes=["A", "B"], rules_status="compatible", quotes=quotes,
        )
        assert row and row["math_arb"] is True
        assert row["arb"] is False
        assert row["validation"]["qualification_reason"] == "SETTLEMENT_STATE_PROOF_FAILED"


def test_line_market_with_losing_gap_fails_closed():
    row = scan_market(
        market_id="gap", event="A @ B", market="totals", bankroll=1000,
        expected_outcomes=["Over", "Under"], rules_status="compatible",
        quotes=[
            {"outcome": "Over", "book": "draftkings", "decimal": 2.2, "point": 4.5},
            {"outcome": "Under", "book": "fanduel", "decimal": 2.2, "point": 3.5},
        ],
    )
    assert row and row["math_arb"] is True
    assert row["arb"] is False
    assert row["minimum_profit"] is None
    assert row["minimum_payout"] is None
    assert row["validation"]["qualification_reason"] == "SETTLEMENT_STATE_PROOF_FAILED"


def test_quote_combination_search_skips_mismatched_line_pair():
    row = scan_market(
        market_id="line-choice", event="A @ B", market="totals", bankroll=1000,
        expected_outcomes=["Over", "Under"], rules_status="compatible",
        quotes=[
            {"outcome": "Over", "book": "a", "decimal": 2.2, "point": 8.5},
            {"outcome": "Over", "book": "b", "decimal": 2.1, "point": 7.5},
            {"outcome": "Under", "book": "c", "decimal": 2.1, "point": 7.5},
        ],
    )
    assert row and row["arb"] is True
    assert {leg["point"] for leg in row["legs"]} == {7.5}


def test_quote_combination_ranks_by_settlement_state_minimum():
    row = scan_market(
        market_id="settlement-ranked", event="A @ B", market="spreads", bankroll=1000,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "quarter-a", "decimal": 2.2, "point": -0.25},
            {"outcome": "B", "book": "quarter-b", "decimal": 2.2, "point": 0.25},
            {"outcome": "A", "book": "half-a", "decimal": 2.18, "point": -0.5},
            {"outcome": "B", "book": "half-b", "decimal": 2.18, "point": 0.5},
        ],
    )
    assert row and row["arb"] is True
    assert {leg["book"] for leg in row["legs"]} == {"half-a", "half-b"}
    assert row["minimum_profit"] == 90.0


def test_unhashable_line_point_fails_closed_without_hiding_valid_pair():
    row = scan_market(
        market_id="malformed-point", event="A @ B", market="totals", bankroll=1000,
        expected_outcomes=["Over", "Under"], rules_status="compatible",
        quotes=[
            {"outcome": "Over", "book": "bad", "decimal": 2.3, "point": [7.5]},
            {"outcome": "Over", "book": "good-over", "decimal": 2.1, "point": 7.5},
            {"outcome": "Under", "book": "good-under", "decimal": 2.1, "point": 7.5},
        ],
    )
    assert row and row["arb"] is True
    assert {leg["book"] for leg in row["legs"]} == {"good-over", "good-under"}


def test_three_way_tie_outcome_does_not_add_synthetic_refund_state():
    proof = prove_quoted_market(
        market="h2h_3_way", push_policy="tie_push",
        legs=[
            {"outcome": "A", "book": "a", "stake": 100, "net_decimal": 3.3},
            {"outcome": "Draw", "book": "b", "stake": 100, "net_decimal": 3.3},
            {"outcome": "B", "book": "c", "stake": 100, "net_decimal": 3.3},
        ],
    )
    assert proof["includes_push"] is False
    assert proof["strict_arbitrage"] is True


def test_audit_candidate_retains_line_point():
    saved = app._audit_candidate({"legs": [{
        "outcome": "Over", "book": "a", "decimal": 2.2, "stake": 50, "point": 4.5,
    }]})
    assert saved["legs"][0]["point"] == 4.5


def test_prove_quoted_market_integer_total_push_zero_is_not_strict():
    proof = prove_quoted_market(
        market="totals",
        legs=[
            {"outcome": "Over", "book": "a", "stake": 50, "net_decimal": 2.2, "point": 8},
            {"outcome": "Under", "book": "b", "stake": 50, "net_decimal": 2.2, "point": 8},
        ],
    )
    assert proof["includes_push"] is True
    assert proof["strict_arbitrage"] is False
    assert proof["minimum_net_pnl"] == 0.0


def test_quarter_line_proof_models_half_push_settlement():
    proof = prove_quoted_market(
        market="spreads",
        legs=[
            {"outcome": "A -0.25", "book": "a", "stake": 500, "net_decimal": 2.2, "point": -0.25},
            {"outcome": "B +0.25", "book": "b", "stake": 500, "net_decimal": 2.2, "point": 0.25},
        ],
    )
    draw = next(row for row in proof["states"] if row["state"] == "RESULT_0")
    assert proof["quarter_line_expanded"] is True
    assert draw["net_pnl"] == 50.0
    assert proof["minimum_net_pnl"] == 50.0


def test_quarter_line_scan_publishes_settlement_state_minimum_profit():
    row = scan_market(
        market_id="quarter", event="A @ B", market="spreads", bankroll=1000,
        expected_outcomes=["A -0.25", "B +0.25"], rules_status="compatible",
        quotes=[
            {"outcome": "A -0.25", "book": "a", "decimal": 2.2, "point": -0.25},
            {"outcome": "B +0.25", "book": "b", "decimal": 2.2, "point": 0.25},
        ],
    )
    assert row and row["arb"] is True
    assert row["minimum_profit"] == 50.0
    assert row["minimum_payout"] == 1050.0
    assert row["validation"]["settlement_states"]["minimum_net_pnl"] == 50.0


def test_declared_moneyline_tie_push_is_included():
    proof = prove_quoted_market(
        market="h2h",
        push_policy="tie_push",
        legs=[
            {"outcome": "A", "book": "a", "stake": 50, "net_decimal": 2.2},
            {"outcome": "B", "book": "b", "stake": 50, "net_decimal": 2.2},
        ],
    )
    assert proof["includes_push"] is True
    assert proof["strict_arbitrage"] is False
    assert proof["minimum_net_pnl"] == 0.0


def test_tied_period_void_is_a_refund_state():
    proof = prove_quoted_market(
        market="h2h_1st_5_innings",
        push_policy="tied_period_void",
        legs=[
            {"outcome": "A", "book": "a", "stake": 50, "net_decimal": 2.2},
            {"outcome": "B", "book": "b", "stake": 50, "net_decimal": 2.2},
        ],
    )
    assert proof["includes_push"] is True
    assert proof["strict_arbitrage"] is False
    assert proof["minimum_net_pnl"] == 0.0


def test_generic_push_policy_does_not_invent_half_point_push():
    row = scan_market(
        market_id="spread", event="A @ B", market="spreads", bankroll=1000,
        expected_outcomes=["A +1.5", "B -1.5"], rules_status="compatible",
        context={"settlement_validation": {"rules": [
            {"book": "draftkings", "push_policy": "push"},
            {"book": "fanduel", "push_policy": "push"},
        ]}},
        quotes=[
            {"outcome": "A +1.5", "book": "draftkings", "decimal": 2.2, "point": 1.5},
            {"outcome": "B -1.5", "book": "fanduel", "decimal": 2.2, "point": -1.5},
        ],
    )
    assert row and row["arb"] is True
    assert row["validation"]["settlement_states"]["includes_push"] is False


def test_generic_push_policy_does_not_invent_overtime_winner_refund_state():
    proof = prove_quoted_market(
        market="h2h",
        push_policy="push",
        legs=[
            {"outcome": "A", "book": "a", "stake": 50, "net_decimal": 2.2},
            {"outcome": "B", "book": "b", "stake": 50, "net_decimal": 2.2},
        ],
    )
    assert proof["includes_push"] is False
    assert proof["strict_arbitrage"] is True
    assert proof["minimum_net_pnl"] == 10.0


def test_market_specific_quarter_line_fails_closed_before_dispatch():
    with pytest.raises(SettlementProofError, match="explicit settlement-state model"):
        prove_quoted_market(
            market="spreads", push_policy="market_specific",
            legs=[
                {"outcome": "A -0.25", "book": "a", "stake": 50, "net_decimal": 2.2, "point": -0.25},
                {"outcome": "B +0.25", "book": "b", "stake": 50, "net_decimal": 2.2, "point": 0.25},
            ],
        )


def test_shortened_game_policy_adds_all_refund_void_state():
    proof = prove_quoted_market(
        market="spreads", push_policy="push",
        shortened_game_policy="9_or_8.5_home_leading_unless_unconditionally_determined",
        legs=[
            {"outcome": "A -1.5", "book": "a", "stake": 50, "net_decimal": 2.2, "point": -1.5},
            {"outcome": "B +1.5", "book": "b", "stake": 50, "net_decimal": 2.2, "point": 1.5},
        ],
    )
    void = next(row for row in proof["states"] if row["state"] == "VOID")
    assert void["net_pnl"] == 0.0
    assert proof["strict_arbitrage"] is False


def test_health_survives_checkpoint_dependency_failure(monkeypatch):
    def fail():
        raise RuntimeError("ddb unavailable")
    monkeypatch.setattr(app, "get_checkpoint", fail)
    response = app.lambda_handler({"httpMethod": "GET", "path": "/v1/arb/health"}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["ok"] is True
    assert body["collector_checkpoint"] is None
    assert body["collector_checkpoint_error"] == "RuntimeError"


def test_non_mlb_baseball_does_not_reuse_mlb_rules():
    assert validation.sport_family("baseball_mlb") == "baseball"
    assert validation.sport_family("baseball_kbo") == "baseball_kbo"
    assert lookup("draftkings", validation.sport_family("baseball_kbo"), "winner", "nj") is None
    assert lookup("betmgm", validation.sport_family("baseball_npb"), "winner", "nj") is None


def test_table_tennis_does_not_reuse_tennis_rules():
    family = validation.sport_family("table_tennis_tt_elite_series")
    assert family == "tabletennis"
    assert lookup("betmgm", family, "spreads", "nj") is None


def test_nba_ncaa_rule_does_not_qualify_euroleague(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "euro", "sport": "basketball_euroleague", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert len(rows) == 1
    assert rows[0]["rules_status"] == "unknown"
    assert rows[0]["context"]["settlement_validation"]["reason"] == "EVENT_OR_MARKET_SCOPE_UNREVIEWED"


def test_north_american_hockey_policy_does_not_qualify_swedish_league(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "shl", "sport": "icehockey_sweden_hockey_league", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert len(rows) == 1
    assert rows[0]["rules_status"] == "unknown"
    assert rows[0]["context"]["settlement_validation"]["reason"] == "EVENT_OR_MARKET_SCOPE_UNREVIEWED"


def test_north_american_hockey_policy_applies_to_nhl(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "nhl", "sport": "icehockey_nhl", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert any(row["rules_status"] == "compatible" for row in rows)


def test_wnba_is_outside_reviewed_nba_ncaa_basketball_scope(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "wnba", "sport": "basketball_wnba", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert all(row["rules_status"] == "unknown" for row in rows)
    assert rows[0]["context"]["settlement_validation"]["reason"] == "EVENT_OR_MARKET_SCOPE_UNREVIEWED"


def test_wncaab_provider_key_is_inside_reviewed_ncaa_scope(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "wncaab", "sport": "basketball_wncaab", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert any(row["rules_status"] == "compatible" for row in rows)


def test_explicit_non_us_hockey_branch_applies_to_swedish_league(monkeypatch):
    monkeypatch.setattr(validation, "assess_quote", lambda quote: {
        "status": "fresh", "fresh": True, "age_seconds": 0, "max_age_seconds": 180,
        "reason": None,
    })
    rows = validation.validate_event({
        "id": "shl-betmgm", "sport": "icehockey_sweden_hockey_league", "market": "h2h",
        "quotes": [
            {"book": "betmgm", "outcome": "A", "decimal": 2.2},
            {"book": "betmgm", "outcome": "B", "decimal": 2.2},
        ],
    }, jurisdiction="nj")
    assert any(row["rules_status"] == "compatible" for row in rows)


def test_market_specific_push_policy_fails_closed():
    with pytest.raises(SettlementProofError, match="explicit settlement-state model"):
        prove_quoted_market(
            market="h2h", push_policy="market_specific",
            legs=[
                {"outcome": "A", "book": "a", "stake": 50, "net_decimal": 2.2},
                {"outcome": "B", "book": "b", "stake": 50, "net_decimal": 2.2},
            ],
        )


def test_settlement_proof_receives_full_precision_odds(monkeypatch):
    observed = []

    def capture(*, market, legs, push_policy=None, shortened_game_policy=None):
        observed.extend(legs)
        return {
            "strict_arbitrage": True, "includes_push": False,
            "minimum_net_pnl": 1.0, "state_count": 2, "states": [],
        }

    monkeypatch.setattr("arb_engine.prove_quoted_market", capture)
    precise = 2.123456789
    row = scan_market(
        market_id="precision", event="A @ B", market="h2h", bankroll=1000,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "a", "decimal": precise},
            {"outcome": "B", "book": "b", "decimal": precise},
        ],
    )
    assert row and observed
    assert observed[0]["net_decimal"] == precise
    assert row["legs"][0]["net_decimal"] == round(precise, 6)


def test_oversized_decimal_odds_hold_candidate_instead_of_raising():
    row = scan_market(
        market_id="oversized-odds", event="A @ B", market="h2h", bankroll=1000,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "a", "decimal": 1e24},
            {"outcome": "B", "book": "b", "decimal": 1e24},
        ],
    )
    assert row and row["arb"] is False and row["executable"] is False
    assert row["validation"]["qualification_reason"] == "SETTLEMENT_STATE_PROOF_FAILED"
