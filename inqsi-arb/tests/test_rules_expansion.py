import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rules import COMPATIBLE, INCOMPATIBLE, UNKNOWN, compatibility, lookup, registry_rows


def test_fanduel_baseball_is_not_wildcarded_from_state_house_rules():
    assert lookup("fanduel", "baseball", "winner", "*") is None
    assert lookup("fanduel", "baseball", "winner", "ny") is not None
    assert lookup("fanduel", "baseball", "winner", "in") is not None


def test_new_york_baseball_core_pair_remains_explicitly_compatible():
    result = compatibility(["draftkings", "fanduel"], "baseball", "winner", "ny")
    assert result["status"] == COMPATIBLE
    assert result["settlement_profile"] == "mlb_full_game_2way_action_v1"


def test_new_york_football_interruption_rules_fail_closed_as_incompatible():
    result = compatibility(["draftkings", "fanduel"], "americanfootball", "winner", "ny")
    assert result["status"] == INCOMPATIBLE
    assert result["reason"] == "SETTLEMENT_PROFILE_CONFLICT"


def test_new_york_basketball_hockey_and_tennis_are_reviewed_but_not_cross_qualified():
    for sport in ("basketball", "icehockey", "tennis"):
        assert lookup("draftkings", sport, "winner", "ny").reviewed is True
        assert lookup("fanduel", sport, "winner", "ny").reviewed is True
        assert compatibility(["draftkings", "fanduel"], sport, "winner", "ny")["status"] == INCOMPATIBLE


def test_unknown_jurisdiction_still_fails_closed_for_state_specific_fanduel_rules():
    result = compatibility(["draftkings", "fanduel"], "basketball", "winner", "nj")
    assert result["status"] == UNKNOWN
    assert "fanduel" in result["missing_books"]


def test_registry_sources_are_current_official_https_pages():
    rows = registry_rows()
    ny = [r for r in rows if r["jurisdiction"] == "ny"]
    assert len(ny) >= 30
    assert all(r["reviewed"] for r in ny)
    assert all(r["version"] == "2026-09-11" for r in ny)
    assert all(r["source"].startswith("https://") for r in ny)
