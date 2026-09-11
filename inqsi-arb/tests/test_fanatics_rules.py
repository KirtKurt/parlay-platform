import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # registers supplemental reviewed rules
from rules import COMPATIBLE, INCOMPATIBLE, UNKNOWN, compatibility, lookup


def test_fanatics_provider_key_has_reviewed_new_york_core_rules():
    for sport in ("baseball", "americanfootball", "basketball", "icehockey", "soccer", "tennis"):
        for family in ("winner", "spreads", "totals"):
            rule = lookup("fanatics", sport, family, "ny")
            assert rule is not None
            assert rule.reviewed is True
            assert rule.source == "https://sportsbook.fanatics.com/legal/ny/house-rules"
            assert rule.version == "2026-09-11"


def test_fanatics_state_rules_do_not_leak_to_other_jurisdictions():
    assert lookup("fanatics", "baseball", "winner", "*") is None
    result = compatibility(["fanatics", "draftkings"], "baseball", "winner", "nj")
    assert result["status"] == UNKNOWN
    assert "fanatics" in result["missing_books"]


def test_fanatics_is_reviewed_but_not_cross_qualified_without_equivalence_proof():
    for sport in ("baseball", "americanfootball", "basketball", "icehockey", "soccer", "tennis"):
        result = compatibility(["fanatics", "draftkings"], sport, "winner", "ny")
        assert result["status"] == INCOMPATIBLE
        assert result["reason"] == "SETTLEMENT_PROFILE_CONFLICT"


def test_single_fanatics_profile_is_internally_reviewed():
    result = compatibility(["fanatics"], "baseball", "winner", "ny")
    assert result["status"] == COMPATIBLE
    assert result["rules"][0]["book"] == "fanatics"


def test_market_classifier_keeps_full_game_families_narrow():
    assert validation.market_family("h2h") == "winner"
    assert validation.market_family("spreads") == "spreads"
    assert validation.market_family("totals") == "totals"
    assert validation.market_family("player_points") == "player_props"
    assert validation.market_family("totals_h1") == "periods"
