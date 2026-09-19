import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import arb_engine
from arb_engine import ArbValidationError, american_to_decimal, scan_all, scan_market
from provider import normalize_games
from app import lambda_handler


def test_two_way_verified_arb_and_cent_reconciliation():
    row = scan_market(
        market_id="x", event="A @ B", market="h2h", bankroll=1000,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "american": 115},
            {"outcome": "A", "book": "two", "american": 105},
            {"outcome": "B", "book": "two", "american": 110},
        ],
    )
    assert row and row["arb"] is True
    assert row["executable"] is True
    assert sum(x["stake"] for x in row["legs"]) == row["allocated_stake"]
    assert row["allocated_stake"] <= 1000
    assert row["minimum_profit"] > 0


def test_three_way_verified_arb():
    row = scan_market(
        market_id="soccer", event="A v B", market="h2h_3_way", bankroll=500,
        expected_outcomes=["A", "Draw", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 3.4},
            {"outcome": "Draw", "book": "two", "decimal": 3.6},
            {"outcome": "B", "book": "three", "decimal": 3.5},
        ],
    )
    assert row and row["arb"]
    assert row["executable"] is True
    assert len(row["legs"]) == 3


def test_infeasible_rounding_keeps_math_but_not_verified():
    row = scan_market(
        market_id="thin", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.2, "min_stake": 60},
            {"outcome": "B", "book": "two", "decimal": 2.2, "min_stake": 60},
        ],
    )
    assert row and row["math_arb"] is True
    assert row["executable"] is False
    assert row["arb"] is False
    assert row["validation"]["qualification_reason"] == "NO_COMBINATION_WITHIN_BANKROLL"


def test_incomplete_outcome_universe_rejected():
    row = scan_market(
        market_id="bad", event="A v B", market="h2h_3_way", bankroll=100,
        expected_outcomes=["A", "Draw", "B"], rules_status="compatible",
        quotes=[{"outcome": "A", "book": "one", "decimal": 3.0}, {"outcome": "B", "book": "two", "decimal": 3.0}],
    )
    assert row and not row["arb"]
    assert row["validation"]["outcome_coverage"] == "incomplete"


def test_unknown_rules_never_labelled_arb():
    row = scan_market(
        market_id="bad-rules", event="A v B", market="x", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="unknown",
        quotes=[{"outcome": "A", "book": "one", "decimal": 2.2}, {"outcome": "B", "book": "two", "decimal": 2.2}],
    )
    assert row and not row["arb"]
    assert row["math_arb"] is True
    assert row["validation"]["qualification_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"


def test_provider_identity_only_is_unverified_not_arb():
    row = scan_market(
        market_id="provider-only", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="provider_identity_only",
        quotes=[{"outcome": "A", "book": "one", "decimal": 2.2}, {"outcome": "B", "book": "two", "decimal": 2.2}],
    )
    assert row and row["math_arb"] is True
    assert row["arb"] is False
    assert row["validation"]["rules_compatible"] is False


def test_default_rules_are_fail_closed():
    row = scan_market(
        market_id="default-rules", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"],
        quotes=[{"outcome": "A", "book": "one", "decimal": 2.2}, {"outcome": "B", "book": "two", "decimal": 2.2}],
    )
    assert row and row["math_arb"] is True and row["arb"] is False


def test_normalizer_groups_opposing_spreads_together():
    games = [{
        "id": "e1", "sport_title": "Test", "home_team": "Home", "away_team": "Away", "commence_time": "2030-01-01T00:00:00Z",
        "bookmakers": [{"key": "book1", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Home", "point": -3.5, "price": -105}, {"name": "Away", "point": 3.5, "price": -105}
        ]}]}],
    }]
    rows = normalize_games(games, sport_key="test")
    assert len(rows) == 1
    assert rows[0]["expected_outcomes"] == ["Away +3.5", "Home -3.5"]
    assert rows[0]["rules_status"] == "provider_identity_only"


def test_normalizer_separates_alternate_total_lines():
    games = [{
        "id": "e1", "home_team": "H", "away_team": "A", "bookmakers": [{"key": "b", "markets": [{"key": "alternate_totals", "outcomes": [
            {"name": "Over", "point": 7.5, "price": 110}, {"name": "Under", "point": 7.5, "price": -120},
            {"name": "Over", "point": 8.5, "price": 125}, {"name": "Under", "point": 8.5, "price": -140},
        ]}]}]
    }]
    rows = normalize_games(games, sport_key="baseball_mlb")
    assert len(rows) == 2
    assert all(len(r["expected_outcomes"]) == 2 for r in rows)


def test_api_health_does_not_claim_betting():
    result = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/health"}, None)
    body = json.loads(result["body"])
    assert result["statusCode"] == 200 and body["places_bets"] is False


def test_scan_all_separates_verified_from_unverified_math_arbs():
    payload = {"bankroll": 1000, "events": [
        {"id": "a", "event": "A", "market": "x", "rules_status": "compatible", "expected_outcomes": ["1", "2"], "quotes": [
            {"outcome": "1", "book": "b1", "decimal": 2.1}, {"outcome": "2", "book": "b2", "decimal": 2.1}]},
        {"id": "b", "event": "B", "market": "x", "rules_status": "unknown", "expected_outcomes": ["1", "2"], "quotes": [
            {"outcome": "1", "book": "b1", "decimal": 2.05}, {"outcome": "2", "book": "b2", "decimal": 2.05}]},
    ]}
    result = scan_all(payload)
    assert result["n_arbs"] == 1
    assert result["n_detected_unverified"] == 1
    assert result["hits"][0]["market_id"] == "a"
    assert result["detected_unverified"][0]["market_id"] == "b"


def test_american_conversion():
    assert round(american_to_decimal(-110), 4) == 1.9091
    assert american_to_decimal(115) == 2.15


def test_limits_scale_the_continuous_plan_before_rounding():
    row = scan_market(
        market_id="capped", event="A v B", market="h2h", bankroll=1000,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.2, "limit": 100},
            {"outcome": "B", "book": "two", "decimal": 2.2, "limit": 100},
        ],
    )
    assert row and row["arb"] is True
    assert row["allocated_stake"] == 200
    assert all(leg["stake"] == 100 for leg in row["legs"])


def test_executable_alternative_quote_can_replace_unusable_top_price():
    row = scan_market(
        market_id="alternative", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "capped", "decimal": 2.2, "limit": 0.5, "stake_increment": 1},
            {"outcome": "A", "book": "usable", "decimal": 2.1},
            {"outcome": "B", "book": "two", "decimal": 2.1},
        ],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert {leg["book"] for leg in row["legs"]} == {"usable", "two"}


def test_executable_ninth_quote_is_not_truncated():
    quotes = [
        {"outcome": "A", "book": f"capped-{index}", "decimal": 2.2,
         "limit": 0.5, "stake_increment": 1}
        for index in range(8)
    ] + [
        {"outcome": "A", "book": "usable", "decimal": 2.1},
        {"outcome": "B", "book": "two", "decimal": 2.1},
    ]
    row = scan_market(
        market_id="ninth", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible", quotes=quotes,
    )
    assert row and row["arb"] is True
    assert {leg["book"] for leg in row["legs"]} == {"usable", "two"}


def test_exact_discrete_thirteen_way_market_can_verify_without_enumeration():
    outcomes = [f"O{index}" for index in range(13)]
    row = scan_market(
        market_id="thirteen", event="Field", market="outright", bankroll=1300,
        expected_outcomes=outcomes, rules_status="compatible",
        quotes=[{"outcome": outcome, "book": f"book-{index}", "decimal": 14.0, "stake_increment": 1}
                for index, outcome in enumerate(outcomes)],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert len(row["legs"]) == 13
    assert all(leg["stake"] == 100 for leg in row["legs"])


def test_non_finite_stake_constraint_rejects_quote_without_crashing_scan():
    row = scan_market(
        market_id="nonfinite", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "bad", "decimal": 2.2, "min_stake": "nan"},
            {"outcome": "B", "book": "two", "decimal": 2.2},
        ],
    )
    assert row is None


def test_asymmetric_constraints_rebalance_beyond_initial_neighborhood():
    row = scan_market(
        market_id="rebalance", event="A v B", market="h2h", bankroll=30,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 1.5, "limit": 7, "stake_increment": 1},
            {"outcome": "B", "book": "two", "decimal": 4.0, "limit": 5, "stake_increment": 2},
        ],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert {leg["stake"] for leg in row["legs"]} == {2, 5}


def test_two_way_search_continues_beyond_256_jointly_infeasible_quotes():
    quotes = [{"outcome": "A", "book": "a", "decimal": 2.1}]
    quotes.extend(
        {"outcome": "B", "book": f"burdened-{index}", "decimal": 2.2, "min_stake": 60}
        for index in range(256)
    )
    quotes.append({"outcome": "B", "book": "usable", "decimal": 2.1})
    row = scan_market(
        market_id="many", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible", quotes=quotes,
    )
    assert row and row["arb"] is True
    assert {leg["book"] for leg in row["legs"]} == {"a", "usable"}


def test_two_way_exact_solver_finds_interior_stake_under_forced_minimum():
    row = scan_market(
        market_id="interior", event="A v B", market="h2h", bankroll=17,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 4.0, "min_stake": 8, "limit": 9, "stake_increment": 4},
            {"outcome": "B", "book": "two", "decimal": 2.2, "limit": 16, "stake_increment": 1},
        ],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert sum(leg["stake"] for leg in row["legs"]) <= 17


def test_sub_cent_stake_increment_is_rejected_before_serialization():
    row = scan_market(
        market_id="subcent", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.1, "limit": 1.005, "stake_increment": 0.001},
            {"outcome": "B", "book": "two", "decimal": 2.1},
        ],
    )
    assert row is None


def test_non_cent_aligned_increment_is_rejected_before_serialization():
    row = scan_market(
        market_id="noncent", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.1, "limit": 1.005, "stake_increment": 0.015},
            {"outcome": "B", "book": "two", "decimal": 2.1},
        ],
    )
    assert row is None


def test_multiway_exact_solver_finds_interior_discrete_plan():
    row = scan_market(
        market_id="three-way-interior", event="A v B", market="h2h_3_way", bankroll=8,
        expected_outcomes=["A", "Draw", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 3.0, "stake_increment": 1},
            {"outcome": "Draw", "book": "two", "decimal": 3.0, "stake_increment": 2},
            {"outcome": "B", "book": "three", "decimal": 6.0, "stake_increment": 1},
        ],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert sorted(leg["stake"] for leg in row["legs"]) == [1, 2, 2]


def test_scalar_books_filter_fails_validation_instead_of_iteration():
    with pytest.raises(ArbValidationError):
        scan_all({"events": [], "books": 123})


def test_exact_solver_keeps_inclusive_bankroll_endpoint():
    row = scan_market(
        market_id="inclusive", event="A v B", market="h2h", bankroll=7,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 6.0, "min_stake": 5, "limit": 5.18, "stake_increment": 5},
            {"outcome": "B", "book": "two", "decimal": 6.0, "stake_increment": 1},
        ],
    )
    assert row and row["arb"] is True
    assert sorted(leg["stake"] for leg in row["legs"]) == [2, 5]


def test_plan_ranking_keeps_break_even_above_losing(monkeypatch):
    row = scan_market(
        market_id="break-even", event="A v B", market="h2h", bankroll=9,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 3.0, "stake_increment": 0.5},
            {"outcome": "B", "book": "two", "decimal": 1.5, "min_stake": 1, "limit": 3.72, "stake_increment": 1},
        ],
    )
    assert row
    assert min(leg["profit_if_wins"] for leg in row["legs"]) >= 0


def test_equivalent_quote_combinations_reuse_exact_search(monkeypatch):
    calls = 0
    original = arb_engine._two_way_exact_plan
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(arb_engine, "_two_way_exact_plan", counted)
    quotes = []
    for outcome in ("A", "B"):
        quotes.extend({"outcome": outcome, "book": f"{outcome}-{index}", "decimal": 2.00001} for index in range(64))
    scan_market(
        market_id="bounded", event="A v B", market="h2h", bankroll=20,
        expected_outcomes=["A", "B"], rules_status="compatible", quotes=quotes,
    )
    assert calls <= 2


def test_scan_all_shares_plan_work_budget_across_markets_and_final_plans(monkeypatch):
    budget_ids = set()
    original = arb_engine._rounded_quote_plan

    def observed(selected, outcomes, bankroll, exact_budget=None, **kwargs):
        assert exact_budget is not None
        budget_ids.add(id(exact_budget))
        return original(selected, outcomes, bankroll, exact_budget, **kwargs)

    monkeypatch.setattr(arb_engine, "_rounded_quote_plan", observed)
    events = []
    for index in range(3):
        events.append({
            "id": f"market-{index}", "event": f"A{index} v B{index}", "market": "h2h",
            "expected_outcomes": ["A", "B"], "rules_status": "compatible",
            "quotes": [
                {"outcome": "A", "book": "one", "decimal": 2.00001},
                {"outcome": "B", "book": "two", "decimal": 2.00001},
            ],
        })
    scan_all({"bankroll": 200, "events": events})
    assert len(budget_ids) == 1


def test_multiway_neighborhood_enumeration_respects_scan_work_budget(monkeypatch):
    calls = 0
    original = arb_engine.optimize_rounding_neighborhood

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(arb_engine, "optimize_rounding_neighborhood", counted)
    outcomes = [f"O{index}" for index in range(10)]
    quotes = [
        {"outcome": outcome, "book": f"book-{outcome}-{copy}", "decimal": 20.1}
        for outcome in outcomes for copy in range(2)
    ]
    row = scan_market(
        market_id="bounded-neighborhood", event="multiway", market="winner", bankroll=100,
        expected_outcomes=outcomes, rules_status="compatible", quotes=quotes,
    )
    assert row and row["math_arb"] is True
    assert row["arb"] is True
    assert calls == 1


def test_oversized_neighborhood_preserves_budget_for_later_markets():
    budget = {"remaining": 100}
    legs = [
        {"outcome": f"O{index}", "book": f"book-{index}", "stake": 10.005,
         "net_decimal": 10.2, "stake_increment": 0.01, "min_stake": 0}
        for index in range(10)
    ]
    result = arb_engine._bounded_rounding_neighborhood(legs, 200, budget)
    assert result["reason"] == "SCAN_PLAN_WORK_BUDGET_EXHAUSTED"
    assert budget["remaining"] == 100


def test_too_many_neighborhood_legs_do_not_charge_budget():
    budget = {"remaining": 20000}
    legs = [
        {"outcome": f"O{index}", "book": f"book-{index}", "stake": 10.005,
         "net_decimal": 20.0, "stake_increment": 0.01, "min_stake": 0}
        for index in range(14)
    ]
    result = arb_engine._bounded_rounding_neighborhood(legs, 200, budget)
    assert result["reason"] == "TOO_MANY_LEGS_FOR_LOCAL_ENUMERATION"
    assert budget["remaining"] == 20000


def test_surebet_constraint_ratio_overflow_fails_closed():
    row = scan_market(
        market_id="overflow-profile", event="A v B", market="h2h", bankroll=1e308,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.1,
             "min_stake": "1e308", "stake_increment": "0.01"},
            {"outcome": "B", "book": "two", "decimal": 2.1},
        ],
    )
    assert row is None


def test_combination_search_reuses_exact_plan_after_budget_exhaustion():
    row = scan_market(
        market_id="reuse-plan", event="A v B", market="h2h", bankroll=200,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 1.5878315585},
            {"outcome": "B", "book": "two", "decimal": 2.7013866301},
        ],
    )
    assert row and row["arb"] is True
    assert row["minimum_profit"] == 0.01


def test_non_arb_does_not_exhaust_exact_budget_before_later_arb():
    result = scan_all({"bankroll": 200, "events": [
        {
            "id": "ordinary", "event": "A v B", "market": "h2h",
            "expected_outcomes": ["A", "B"], "rules_status": "compatible",
            "quotes": [
                {"outcome": "A", "book": "one", "decimal": 1.9},
                {"outcome": "B", "book": "two", "decimal": 1.9},
            ],
        },
        {
            "id": "arb", "event": "C v D", "market": "h2h",
            "expected_outcomes": ["C", "D"], "rules_status": "compatible",
            "quotes": [
                {"outcome": "C", "book": "three", "decimal": 2.2},
                {"outcome": "D", "book": "four", "decimal": 2.0},
            ],
        },
    ]})
    assert [row["market_id"] for row in result["hits"]] == ["arb"]


def test_multiway_solver_prunes_large_grid_by_active_bounds():
    row = scan_market(
        market_id="large-grid", event="A v B v C", market="h2h_3_way", bankroll=12,
        expected_outcomes=["A", "B", "C"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.5, "stake_increment": 0.02},
            {"outcome": "B", "book": "two", "decimal": 4.0, "stake_increment": 0.01},
            {"outcome": "C", "book": "three", "decimal": 8.0, "stake_increment": 2, "min_stake": 4},
        ],
    )
    assert row and row["arb"] is True and row["allocated_stake"] <= 12


def test_multiway_solver_searches_interior_bounded_grid():
    row = scan_market(
        market_id="interior-grid", event="A v B v C v D", market="winner", bankroll=18,
        expected_outcomes=["A", "B", "C", "D"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 4.70861339,
             "stake_increment": 0.5, "min_stake": 0.5, "limit": 3.5},
            {"outcome": "B", "book": "two", "decimal": 5.5291653,
             "stake_increment": 0.5, "min_stake": 1.5, "limit": 4},
            {"outcome": "C", "book": "three", "decimal": 8.35831113,
             "stake_increment": 1, "min_stake": 1, "limit": 3},
            {"outcome": "D", "book": "four", "decimal": 2.34327911,
             "stake_increment": 2, "min_stake": 2, "limit": 12},
        ],
    )
    assert row and row["arb"] is True and row["executable"] is True
    assert row["minimum_profit"] >= 0.32


def test_duplicate_capped_quotes_do_not_hide_usable_profile():
    quotes = []
    for outcome in ("A", "B"):
        quotes.extend({
            "outcome": outcome, "book": f"{outcome}-capped-{index}",
            "decimal": 2.2, "limit": 0.01,
        } for index in range(64))
        quotes.append({"outcome": outcome, "book": f"{outcome}-usable", "decimal": 2.1})
    row = scan_market(
        market_id="profile-dedupe", event="A v B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], rules_status="compatible", quotes=quotes,
    )
    assert row and row["arb"] is True
    assert {leg["book"] for leg in row["legs"]} == {"A-usable", "B-usable"}


def test_surebet_increment_cent_check_cannot_overflow():
    row = scan_market(
        market_id="overflow-increment", event="A v B", market="h2h", bankroll=1e308,
        expected_outcomes=["A", "B"], rules_status="compatible",
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.1, "stake_increment": "1e308"},
            {"outcome": "B", "book": "two", "decimal": 2.1},
        ],
    )
    assert row is None
