import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arb_engine import scan_all
from constraints import apply_book_constraints
from lifecycle import record_leg, outcome_pnl
from market_catalog import candidate_markets_for_sport, expand_market_families
from rules import registry_rows, registry_size
from validation import market_family, validate_event, validate_events
from app import lambda_handler


def test_documented_market_catalog_is_broad():
    mlb = candidate_markets_for_sport("baseball_mlb")
    nfl = candidate_markets_for_sport("americanfootball_nfl")
    soccer = candidate_markets_for_sport("soccer_epl")
    assert "pitcher_strikeouts" in mlb
    assert "player_pass_yds" in nfl
    assert "correct_score" in soccer
    assert "h2h_1st_5_innings" in mlb
    assert len(set(mlb)) == len(mlb)


def test_family_expansion_has_periods_and_props():
    rows = expand_market_families(["periods", "player_props"])
    assert "h2h_q1" in rows
    assert "batter_hits" in rows
    assert "player_points" in rows


def test_reviewed_registry_is_versioned_and_sourced():
    assert registry_size() >= 6
    rows = registry_rows()
    assert all(r["reviewed"] for r in rows)
    assert all(r["version"] and r["source"].startswith("https://") for r in rows)
    assert {r["book"] for r in rows} >= {"draftkings", "fanduel"}


def test_unknown_settlement_rules_fail_closed():
    event = {
        "sport": "baseball_mlb", "market": "h2h",
        "quotes": [{"book": "unknownbook", "outcome": "A", "decimal": 2.2}, {"book": "other", "outcome": "B", "decimal": 2.2}],
        "expected_outcomes": ["A", "B"], "id": "x", "event": "A @ B",
    }
    validated = validate_event(event)
    assert len(validated) == 1 and validated[0]["rules_status"] == "unknown"
    result = scan_all({"bankroll": 100, "events": validated})
    assert result["n_arbs"] == 0
    assert result["n_detected_unverified"] == 1


def test_unreviewed_book_does_not_poison_reviewed_pair():
    event = {
        "sport": "baseball_mlb", "market": "h2h", "id": "g1", "event": "A @ B",
        "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "draftkings", "outcome": "A", "decimal": 2.2},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2},
            {"book": "unknownbook", "outcome": "A", "decimal": 9.9},
        ],
    }
    rows = validate_events([event])
    compatible = [r for r in rows if r["rules_status"] == "compatible"]
    assert len(compatible) == 1
    assert {q["book"] for q in compatible[0]["quotes"]} == {"draftkings", "fanduel"}
    assert compatible[0]["context"]["excluded_unreviewed_books"] == ["unknownbook"]
    result = scan_all({"bankroll": 100, "events": rows})
    assert result["n_arbs"] == 1


def test_period_classification_precedes_total_and_spread():
    assert market_family("totals_h1") == "periods"
    assert market_family("spreads_1st_5_innings") == "periods"


def test_constraint_analysis_flags_cap():
    result = apply_book_constraints(
        [{"book": "a", "stake": 60, "limit": 100}, {"book": "b", "stake": 40}],
        {"a": {"balance": 50}, "b": {"balance": 100}},
    )
    assert result["feasible"] is False
    assert result["legs"][0]["constraint_status"] == "EXCEEDS_CAP"


def test_position_lifecycle_recalculates_actual_outcome_pnl():
    pos = {"required_outcomes": ["A", "B"], "accepted_legs": [], "state": "REVIEWED"}
    pos = record_leg(pos, {"outcome": "A", "book": "one", "stake": 50, "decimal": 2.2})
    assert pos["state"] == "LEG_RECORDED"
    pos = record_leg(pos, {"outcome": "B", "book": "two", "stake": 50, "decimal": 2.2})
    assert pos["state"] == "COMPLETE"
    pnl = outcome_pnl(pos)
    assert pnl["A"] == 10.0 and pnl["B"] == 10.0


def test_v3_health_rules_and_ui_routes():
    h = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/health"}, None)
    body = json.loads(h["body"])
    assert body["version"] == "INQSI-ARB-v3"
    assert body["automatic_market_discovery"] is True
    assert body["rules_registry_entries"] >= 6
    rules = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/rules"}, None)
    rules_body = json.loads(rules["body"])
    assert rules["statusCode"] == 200 and rules_body["count"] >= 6
    ui = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/ui"}, None)
    assert ui["statusCode"] == 200
    assert "text/html" in ui["headers"]["content-type"]
    assert "Inqsi ARB" in ui["body"]
