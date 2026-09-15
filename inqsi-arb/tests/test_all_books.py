import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # noqa: F401
from app import lambda_handler
from arb_engine import scan_market
from provider_books import catalog_summary
from rules import COMPATIBLE, INCOMPATIBLE, UNKNOWN, compatibility, lookup
from settlement_matrix import prove_quoted_market


def test_catalog_lists_every_us_odds_api_book():
    summary = catalog_summary()
    keys = {row["key"] for row in summary["books"]}
    for key in (
        "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics",
        "espnbet", "betrivers", "hardrockbet", "ballybet", "betparx",
        "bovada", "pinnacle", "prizepicks", "kalshi",
    ):
        assert key in keys
    assert summary["n_us_licensed"] >= 15
    assert all(row["on_math_board"] is True for row in summary["books"])


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


def test_betmgm_nj_baseball_crosses_draftkings_on_shared_5_inning_class():
    assert lookup("betmgm", "baseball", "winner", "nj") is not None
    result = compatibility(["draftkings", "betmgm"], "baseball", "winner", "nj")
    assert result["status"] == COMPATIBLE
    assert result["settlement_profile"] == "mlb_full_game_2way_action_v1"


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
