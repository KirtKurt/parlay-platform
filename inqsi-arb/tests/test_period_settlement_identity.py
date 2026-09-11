from datetime import datetime, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rules import lookup
from validation import market_family, settlement_market_family, validate_event


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def test_period_discovery_family_stays_broad_but_settlement_identity_is_narrow():
    assert market_family("h2h_1st_5_innings") == "periods"
    assert market_family("h2h_3_way_1st_5_innings") == "periods"
    assert market_family("spreads_1st_5_innings") == "periods"
    assert market_family("totals_1st_5_innings") == "periods"
    assert market_family("team_totals_1st_5_innings") == "periods"

    assert settlement_market_family("h2h_1st_5_innings") == "period_winner_2way"
    assert settlement_market_family("h2h_3_way_1st_5_innings") == "period_winner_3way"
    assert settlement_market_family("spreads_1st_5_innings") == "period_spreads"
    assert settlement_market_family("alternate_spreads_1st_5_innings") == "period_spreads"
    assert settlement_market_family("totals_1st_5_innings") == "period_totals"
    assert settlement_market_family("team_totals_1st_5_innings") == "period_team_totals"


def test_fanatics_ny_period_winner_profiles_are_distinct_and_reviewed():
    two_way = lookup("fanatics", "baseball", "period_winner_2way", "ny")
    three_way = lookup("fanatics", "baseball", "period_winner_3way", "ny")
    assert two_way is not None and two_way.reviewed is True
    assert three_way is not None and three_way.reviewed is True
    assert two_way.settlement_profile != three_way.settlement_profile
    assert two_way.push_policy == "tied_period_void"
    assert three_way.push_policy == "tie_selection_wins"
    assert lookup("fanatics", "baseball", "period_totals", "ny") is None
    assert lookup("fanatics", "baseball", "period_spreads", "ny") is None


def test_fanatics_ny_two_way_inning_moneyline_can_be_reviewed_without_widening_other_books():
    ts = fresh_ts()
    fanatics_event = {
        "sport": "baseball_mlb",
        "market": "h2h_1st_5_innings",
        "id": "period-fanatics",
        "event": "A @ B",
        "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "fanatics", "outcome": "A", "decimal": 1.95, "last_update": ts},
            {"book": "fanatics", "outcome": "B", "decimal": 1.95, "last_update": ts},
        ],
    }
    rows = validate_event(fanatics_event, jurisdiction="ny")
    assert len(rows) == 1
    assert rows[0]["rules_status"] == "compatible"
    assert rows[0]["context"]["market_family"] == "period_winner_2way"

    draftkings_event = dict(fanatics_event)
    draftkings_event["id"] = "period-dk"
    draftkings_event["quotes"] = [
        {"book": "draftkings", "outcome": "A", "decimal": 1.95, "last_update": ts},
        {"book": "draftkings", "outcome": "B", "decimal": 1.95, "last_update": ts},
    ]
    rows = validate_event(draftkings_event, jurisdiction="ny")
    assert len(rows) == 1
    assert rows[0]["rules_status"] == "unknown"
