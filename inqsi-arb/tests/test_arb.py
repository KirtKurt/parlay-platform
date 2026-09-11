import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arb_engine import american_to_decimal, scan_all, scan_market
from provider import normalize_games
from app import lambda_handler


def test_two_way_arb_and_cent_reconciliation():
    row = scan_market(
        market_id="x", event="A @ B", market="h2h", bankroll=1000,
        expected_outcomes=["A", "B"],
        quotes=[
            {"outcome": "A", "book": "one", "american": 115},
            {"outcome": "A", "book": "two", "american": 105},
            {"outcome": "B", "book": "two", "american": 110},
        ],
    )
    assert row and row["arb"] is True
    assert sum(x["stake"] for x in row["legs"]) == 1000
    assert row["minimum_profit"] > 0


def test_three_way_arb():
    row = scan_market(
        market_id="soccer", event="A v B", market="h2h_3_way", bankroll=500,
        expected_outcomes=["A", "Draw", "B"],
        quotes=[
            {"outcome": "A", "book": "one", "decimal": 3.4},
            {"outcome": "Draw", "book": "two", "decimal": 3.6},
            {"outcome": "B", "book": "three", "decimal": 3.5},
        ],
    )
    assert row and row["arb"]
    assert len(row["legs"]) == 3


def test_incomplete_outcome_universe_rejected():
    row = scan_market(
        market_id="bad", event="A v B", market="h2h_3_way", bankroll=100,
        expected_outcomes=["A", "Draw", "B"],
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


def test_scan_all_ranks_feasible_profit():
    payload = {"bankroll": 1000, "events": [
        {"id": "a", "event": "A", "market": "x", "expected_outcomes": ["1", "2"], "quotes": [
            {"outcome": "1", "book": "b1", "decimal": 2.1}, {"outcome": "2", "book": "b2", "decimal": 2.1}]},
        {"id": "b", "event": "B", "market": "x", "expected_outcomes": ["1", "2"], "quotes": [
            {"outcome": "1", "book": "b1", "decimal": 2.05}, {"outcome": "2", "book": "b2", "decimal": 2.05}]},
    ]}
    result = scan_all(payload)
    assert result["n_arbs"] == 2
    assert result["hits"][0]["minimum_profit"] >= result["hits"][1]["minimum_profit"]


def test_american_conversion():
    assert round(american_to_decimal(-110), 4) == 1.9091
    assert american_to_decimal(115) == 2.15
