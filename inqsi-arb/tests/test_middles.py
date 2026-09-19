import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arb_engine import scan_all, scan_market
import middle_engine
from middle_engine import detect_middles
from provider import normalize_games


def test_total_risk_middle_is_not_labelled_arb():
    events = [{
        "id": "e1|totals|8.5", "event_id": "e1", "event": "A @ B", "market": "totals",
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "draftkings", "decimal": 1.91, "point": 8.5},
            {"outcome": "Under", "name": "Under", "book": "draftkings", "decimal": 1.91, "point": 8.5},
        ],
    }, {
        "id": "e1|totals|9.5", "event_id": "e1", "event": "A @ B", "market": "totals",
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "fanduel", "decimal": 1.91, "point": 9.5},
            {"outcome": "Under", "name": "Under", "book": "fanduel", "decimal": 1.91, "point": 9.5},
        ],
    }]
    middles = detect_middles(events, bankroll=100)
    assert middles
    row = middles[0]
    assert row["kind"] == "risk_middle"
    assert row["arb"] is False
    assert row["math_arb"] is False
    assert row["gap"] == 1.0
    assert {leg["book"] for leg in row["legs"]} == {"draftkings", "fanduel"}


def test_total_free_middle_is_math_but_not_verified():
    events = [{
        "id": "e2|totals", "event_id": "e2", "event": "A @ B", "market": "totals",
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "draftkings", "decimal": 2.2, "point": 8.5},
            {"outcome": "Under", "name": "Under", "book": "fanduel", "decimal": 2.2, "point": 9.5},
        ],
    }]
    result = scan_all({"bankroll": 100, "events": events})
    assert result["n_middles"] == 1
    row = result["middles"][0]
    assert row["kind"] == "free_middle"
    assert row["math_arb"] is True
    assert row["arb"] is False
    assert row["pnl_if_middle_hits"] > row["minimum_miss_pnl"] > 0


def test_spread_middle_requires_opposite_sides_and_positive_gap():
    events = [{
        "id": "e3|spreads", "event_id": "e3", "event": "Away @ Home", "market": "spreads",
        "quotes": [
            {"outcome": "Home -3.5", "name": "Home", "book": "draftkings", "decimal": 1.91, "point": -3.5},
            {"outcome": "Away +4.5", "name": "Away", "book": "fanduel", "decimal": 1.91, "point": 4.5},
            {"outcome": "Home -3.5", "name": "Home", "book": "betmgm", "decimal": 1.91, "point": -3.5},
            {"outcome": "Away +3.5", "name": "Away", "book": "betmgm", "decimal": 1.91, "point": 3.5},
        ],
    }]
    middles = detect_middles(events, bankroll=200)
    assert middles
    assert all(row["gap"] > 0 for row in middles)
    assert all(row["arb"] is False for row in middles)
    assert any(leg["point"] == -3.5 and other["point"] == 4.5
               for row in middles for leg in row["legs"] for other in row["legs"])


def test_same_line_is_surebet_not_middle():
    events = [{
        "id": "e4|totals", "event_id": "e4", "event": "A @ B", "market": "totals",
        "expected_outcomes": ["Over", "Under"],
        "quotes": [
            {"outcome": "Over", "name": "Over", "book": "one", "decimal": 2.2, "point": 8.5},
            {"outcome": "Under", "name": "Under", "book": "two", "decimal": 2.2, "point": 8.5},
        ],
    }]
    result = scan_all({"bankroll": 100, "events": events})
    assert result["n_middles"] == 0
    assert result["n_detected_unverified"] == 1
    assert result["detected_unverified"][0]["math_arb"] is True


def test_user_book_filter_limits_middle_and_surebet_books():
    events = [{
        "id": "e5|h2h", "event_id": "e5", "event": "A @ B", "market": "h2h",
        "expected_outcomes": ["A", "B"],
        "quotes": [
            {"outcome": "A", "book": "draftkings", "decimal": 2.2},
            {"outcome": "B", "book": "fanduel", "decimal": 2.2},
            {"outcome": "B", "book": "pinnacle", "decimal": 3.0},
        ],
    }]
    open_scan = scan_all({"bankroll": 100, "events": events})
    filtered = scan_all({"bankroll": 100, "events": events, "books": "draftkings,fanduel"})
    assert open_scan["detected_unverified"][0]["legs"][1]["book"] == "pinnacle"
    assert {leg["book"] for leg in filtered["detected_unverified"][0]["legs"]} == {"draftkings", "fanduel"}


def test_normalizer_preserves_point_for_middle_detection():
    games = [{
        "id": "e1", "home_team": "H", "away_team": "A",
        "bookmakers": [{"key": "dk", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "point": 8.5, "price": 100},
            {"name": "Under", "point": 8.5, "price": -120},
        ]}]}, {"key": "fd", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "point": 9.5, "price": 100},
            {"name": "Under", "point": 9.5, "price": -120},
        ]}]}],
    }]
    rows = normalize_games(games, sport_key="baseball_mlb")
    assert all("point" in q for row in rows for q in row["quotes"])
    middles = detect_middles(rows, bankroll=100)
    assert middles and middles[0]["gap"] == 1.0


def test_scan_market_book_filter():
    row = scan_market(
        market_id="x", event="A @ B", market="h2h", bankroll=100,
        expected_outcomes=["A", "B"], books=["one", "two"],
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 2.2},
            {"outcome": "B", "book": "two", "decimal": 2.2},
            {"outcome": "B", "book": "three", "decimal": 5.0},
        ],
    )
    assert row and {leg["book"] for leg in row["legs"]} == {"one", "two"}


def test_middle_grouping_preserves_contract_identity():
    events = [
        {"id": "e6|totals", "event_id": "e6", "event": "A @ B", "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 1.91, "point": 8.5},
        ]},
        {"id": "e6|totals_h1", "event_id": "e6", "event": "A @ B", "market": "totals_h1", "quotes": [
            {"outcome": "Under", "book": "two", "decimal": 1.91, "point": 9.5},
        ]},
        {"id": "e6|player_points", "event_id": "e6", "event": "A @ B", "market": "player_points", "quotes": [
            {"description": "Player A", "outcome": "Over", "book": "one", "decimal": 1.91, "point": 20.5},
        ]},
        {"id": "e6|player_rebounds", "event_id": "e6", "event": "A @ B", "market": "player_rebounds", "quotes": [
            {"description": "Player A", "outcome": "Under", "book": "two", "decimal": 1.91, "point": 21.5},
        ]},
    ]
    assert detect_middles(events, bankroll=100) == []


def test_middle_grouping_folds_alternate_variant_of_same_contract():
    events = [
        {"id": "e7|totals", "event_id": "e7", "event": "A @ B", "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 1.91, "point": 8.5},
        ]},
        {"id": "e7|alternate_totals", "event_id": "e7", "event": "A @ B", "market": "alternate_totals", "quotes": [
            {"outcome": "Under", "book": "two", "decimal": 1.91, "point": 9.5},
        ]},
    ]
    assert len(detect_middles(events, bankroll=100)) == 1


def test_integer_scoring_rejects_gap_with_no_both_win_result():
    totals = [{"id": "e8", "event_id": "e8", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 8.5},
    ]}]
    spreads = [{"id": "e9", "event_id": "e9", "event": "A @ B", "market": "spreads", "quotes": [
        {"outcome": "A -3", "book": "one", "decimal": 2.2, "point": -3},
        {"outcome": "B +3.5", "book": "two", "decimal": 2.2, "point": 3.5},
    ]}]
    assert detect_middles(totals, bankroll=100) == []
    assert detect_middles(spreads, bankroll=100) == []


def test_free_middle_requires_positive_rounded_miss_pnl():
    events = [{"id": "e10", "event_id": "e10", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 1.6053, "point": 8.5},
        {"outcome": "Under", "book": "two", "decimal": 2.6523, "point": 9.5},
    ]}]
    row = detect_middles(events, bankroll=100)[0]
    assert row["minimum_miss_pnl"] <= 0
    assert row["kind"] == "risk_middle"
    assert row["math_arb"] is False


def test_book_filter_applies_to_exchange_pending():
    result = scan_all({"books": "draftkings", "events": [{
        "id": "e11", "event": "A @ B", "market": "h2h_lay", "quotes": [
            {"outcome": "A", "book": "betfair", "decimal": 2.1},
            {"outcome": "A", "book": "draftkings", "decimal": 2.0},
        ],
    }]})
    assert result["n_exchange_pending"] == 1
    assert result["exchange_pending"][0]["books"] == ["draftkings"]


def test_middle_plan_enforces_caps_and_discrete_stakes_before_free_label():
    events = [{"id": "e12", "event_id": "e12", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5, "limit": 1},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5, "limit": 1},
    ]}]
    row = detect_middles(events, bankroll=100)[0]
    assert row["kind"] == "free_middle"
    assert row["allocated_stake"] == 2
    assert all(leg["stake"] == 1 for leg in row["legs"])

    for quote in events[0]["quotes"]:
        quote["stake_increment"] = 5
    assert detect_middles(events, bankroll=100) == []


def test_middle_market_id_contains_normalized_contract():
    events = [{"id": "e13", "event_id": "e13", "event": "A @ B", "market": "alternate_totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5},
    ]}]
    assert "|totals|" in detect_middles(events, bankroll=100)[0]["market_id"]


def test_fractional_fantasy_scoring_allows_attainable_middle():
    events = [{"id": "e14", "event_id": "e14", "event": "A @ B", "market": "player_fantasy_points", "quotes": [
        {"description": "Player A", "outcome": "Over", "book": "one", "decimal": 2.2, "point": 10},
        {"description": "Player A", "outcome": "Under", "book": "two", "decimal": 2.2, "point": 10.5},
    ]}]
    assert len(detect_middles(events, bankroll=100)) == 1


def test_event_fallback_does_not_mix_rematches():
    events = [
        {"event": "A @ B", "commence_time": "2030-01-01T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5},
        ]},
        {"event": "A @ B", "commence_time": "2030-01-02T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5},
        ]},
    ]
    assert detect_middles(events, bankroll=100) == []


def test_event_fallback_groups_market_specific_ids_at_same_start():
    events = [
        {"id": "game|totals|8.5", "event": "A @ B", "commence_time": "2030-01-01T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5},
        ]},
        {"id": "game|totals|9.5", "event": "A @ B", "commence_time": "2030-01-01T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5},
        ]},
    ]
    assert len(detect_middles(events, bankroll=100)) == 1


def test_unidentified_participant_props_do_not_pair():
    events = [{"id": "e15", "event_id": "e15", "event": "A @ B", "market": "player_points", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 10.5},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 11.5},
    ]}]
    assert detect_middles(events, bankroll=100) == []


def test_malformed_middle_limit_fails_closed():
    events = [{"id": "e16", "event_id": "e16", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5, "limit": "abc"},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5},
    ]}]
    assert detect_middles(events, bankroll=100) == []


def test_large_middle_odds_arithmetic_failure_is_contained():
    events = [{"id": "e17", "event_id": "e17", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 1e100, "point": 8.5},
        {"outcome": "Under", "book": "two", "decimal": 1e100, "point": 9.5},
    ]}]
    assert detect_middles(events, bankroll=100) == []


def test_event_fallback_includes_sport_identity():
    events = [
        {"sport": "basketball_nba", "event": "United States @ Canada", "commence_time": "2030-01-01T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5},
        ]},
        {"sport": "icehockey_nhl", "event": "United States @ Canada", "commence_time": "2030-01-01T00:00:00Z", "market": "totals", "quotes": [
            {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5},
        ]},
    ]
    assert detect_middles(events, bankroll=100) == []


def test_middle_exact_solver_finds_interior_stake_plan():
    events = [{"id": "e18", "event_id": "e18", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 4.0, "point": 8.5,
         "min_stake": 8, "limit": 9, "stake_increment": 4},
        {"outcome": "Under", "book": "two", "decimal": 2.2, "point": 9.5,
         "limit": 16, "stake_increment": 1},
    ]}]
    row = detect_middles(events, bankroll=17)[0]
    assert row["kind"] == "free_middle"
    assert row["legs"][0]["stake"] == 8
    assert sum(leg["stake"] for leg in row["legs"]) <= 17


def test_risk_middle_exact_solver_finds_feasible_interior_plan():
    events = [{"id": "e19", "event_id": "e19", "event": "A @ B", "market": "totals", "quotes": [
        {"outcome": "Over", "book": "one", "decimal": 4.0, "point": 8.5,
         "min_stake": 8, "limit": 9, "stake_increment": 4},
        {"outcome": "Under", "book": "two", "decimal": 1.3, "point": 10.5,
         "limit": 16, "stake_increment": 1},
    ]}]
    row = detect_middles(events, bankroll=17)[0]
    assert row["kind"] == "risk_middle"
    assert row["legs"][0]["stake"] == 8
    assert sum(leg["stake"] for leg in row["legs"]) <= 17


def test_middle_pair_optimization_is_bounded(monkeypatch):
    calls = 0
    original = middle_engine._stake_plan
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(middle_engine, "_stake_plan", counted)
    quotes = [
        {"outcome": "Over", "book": f"over-{index}", "decimal": 1.91, "point": 8.5}
        for index in range(100)
    ] + [
        {"outcome": "Under", "book": f"under-{index}", "decimal": 1.91, "point": 9.5}
        for index in range(100)
    ]
    detect_middles([{"id": "bounded-middle", "event_id": "bounded-middle", "event": "A @ B", "market": "totals", "quotes": quotes}], bankroll=100)
    assert calls <= middle_engine.MAX_PAIR_EVALUATIONS


def test_middle_pair_cap_is_applied_after_gap_filtering():
    quotes = [
        {"outcome": "Over", "book": f"over-{index}", "decimal": 1.91, "point": 10}
        for index in range(64)
    ] + [
        {"outcome": "Over", "book": "valid-over", "decimal": 1.91, "point": 0}
    ] + [
        {"outcome": "Under", "book": f"under-{index}", "decimal": 1.91, "point": 5}
        for index in range(65)
    ]
    rows = detect_middles([{
        "id": "eligible-after-cap", "event_id": "eligible-after-cap", "event": "A @ B",
        "market": "totals", "quotes": quotes,
    }], bankroll=100)
    assert rows
    assert all(row["legs"][0]["point"] == 0 for row in rows)


def test_middle_exact_search_uses_one_aggregate_budget(monkeypatch):
    budget_ids = set()
    original = middle_engine._two_way_feasible_plan
    def observed(legs, bankroll, budget=None):
        assert budget is not None
        budget_ids.add(id(budget))
        return original(legs, bankroll, budget)
    monkeypatch.setattr(middle_engine, "_two_way_feasible_plan", observed)
    quotes = []
    for index in range(64):
        quotes.extend([
            {"outcome": "Over", "book": f"over-{index}", "decimal": 4.0, "point": 8.5,
             "min_stake": 8, "limit": 9, "stake_increment": 4},
            {"outcome": "Under", "book": f"under-{index}", "decimal": 1.3, "point": 10.5,
             "limit": 16, "stake_increment": 1},
        ])
    detect_middles([{
        "id": "aggregate-budget", "event_id": "aggregate-budget", "event": "A @ B",
        "market": "totals", "quotes": quotes,
    }], bankroll=17)
    assert len(budget_ids) == 1


def test_middle_pair_budget_is_shared_across_groups(monkeypatch):
    calls = 0
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"feasible": False}
    monkeypatch.setattr(middle_engine, "_stake_plan", counted)
    events = []
    for event_index in range(2):
        quotes = [
            {"outcome": "Over", "book": f"o-{index}", "decimal": 1.91, "point": 0}
            for index in range(65)
        ] + [
            {"outcome": "Under", "book": f"u-{index}", "decimal": 1.91, "point": 5}
            for index in range(65)
        ]
        events.append({
            "id": f"group-{event_index}", "event_id": f"group-{event_index}",
            "event": f"A{event_index} @ B{event_index}", "market": "totals", "quotes": quotes,
        })
    detect_middles(events, bankroll=100)
    assert calls == middle_engine.MAX_PAIR_EVALUATIONS


def test_executable_duplicate_quote_survives_better_unusable_price():
    rows = detect_middles([{
        "id": "duplicate-profile", "event_id": "duplicate-profile", "event": "A @ B",
        "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5,
             "limit": 1, "stake_increment": 5},
            {"outcome": "Over", "book": "one", "decimal": 2.1, "point": 8.5},
            {"outcome": "Under", "book": "two", "decimal": 2.1, "point": 9.5},
        ],
    }], bankroll=100)
    assert rows
    assert rows[0]["kind"] == "free_middle"
    assert sorted(leg["decimal"] for leg in rows[0]["legs"]) == [2.1, 2.1]


def test_infeasible_execution_profiles_do_not_spend_pair_budget():
    quotes = [
        {"outcome": "Over", "book": "one", "decimal": 2.2, "point": 8.5,
         "limit": index / 1000, "stake_increment": 5}
        for index in range(1, 129)
    ] + [
        {"outcome": "Over", "book": "one", "decimal": 2.1, "point": 8.5},
        {"outcome": "Under", "book": "two", "decimal": 2.1, "point": 9.5},
    ]
    rows = detect_middles([{
        "id": "profile-budget", "event_id": "profile-budget", "event": "A @ B",
        "market": "totals", "quotes": quotes,
    }], bankroll=100)
    assert rows
    assert rows[0]["kind"] == "free_middle"
    assert sorted(leg["decimal"] for leg in rows[0]["legs"]) == [2.1, 2.1]


def test_raw_middle_pair_inspections_have_a_scan_wide_cap(monkeypatch):
    calls = 0
    original = middle_engine._scoring_increment

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(middle_engine, "_scoring_increment", counted)
    quotes = [
        {"outcome": "Over", "book": f"over-{index}", "decimal": 1.91,
         "point": 0, "scoring_increment": 1000}
        for index in range(200)
    ] + [
        {"outcome": "Under", "book": f"under-{index}", "decimal": 1.91,
         "point": 5, "scoring_increment": 1000}
        for index in range(200)
    ]
    assert detect_middles([{
        "id": "raw-budget", "event_id": "raw-budget", "event": "A @ B",
        "market": "totals", "quotes": quotes,
    }], bankroll=100) == []
    assert calls == middle_engine.MAX_RAW_PAIR_INSPECTIONS


def test_middle_constraint_ratio_overflow_fails_closed():
    rows = detect_middles([{
        "id": "overflow-profile", "event_id": "overflow-profile", "event": "A @ B",
        "market": "totals", "quotes": [
            {"outcome": "Over", "book": "one", "decimal": 2.1, "point": 8.5,
             "min_stake": "1e308", "stake_increment": "0.01"},
            {"outcome": "Under", "book": "two", "decimal": 2.1, "point": 9.5},
        ],
    }], bankroll=1e308)
    assert rows == []


def test_ui_marks_free_middles_as_settlement_unverified():
    from ui_page import HTML
    assert "UNVERIFIED FREE MIDDLE" in HTML
    assert "MIDDLE_REQUIRES_CROSS_LINE_SETTLEMENT_REVIEW" in HTML
    assert "Free middles lock a profit" not in HTML
