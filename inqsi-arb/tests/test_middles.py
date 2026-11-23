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
    assert result["n_arbs"] == 1
    assert result["n_detected_unverified"] == 0
    assert result["hits"][0]["math_arb"] is True
    assert result["hits"][0]["validation"]["settlement_advisory"] == "SPORTSBOOK_RULES_MAY_AFFECT_SETTLEMENT"


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
    assert open_scan["n_arbs"] == 1
    assert open_scan["hits"][0]["legs"][1]["book"] == "pinnacle"
    assert {leg["book"] for leg in filtered["hits"][0]["legs"]} == {"draftkings", "fanduel"}
