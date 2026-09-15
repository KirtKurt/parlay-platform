import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # noqa: F401  registers state packs
from app import lambda_handler
from collector import collect_tick
from quote_store import get_snapshot, put_snapshot, reset_memory
from rules import COMPATIBLE, UNKNOWN, compatibility, lookup
from state_packs import licensed_books, pack_summary


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def test_arizona_baseball_cross_book_is_compatible_after_house_rule_review():
    assert lookup("fanduel", "baseball", "winner", "az") is not None
    result = compatibility(["draftkings", "fanduel"], "baseball", "winner", "az")
    assert result["status"] == COMPATIBLE
    assert result["settlement_profile"] == "mlb_full_game_2way_action_v1"


def test_unread_fanduel_state_still_fails_closed():
    result = compatibility(["draftkings", "fanduel"], "baseball", "winner", "co")
    assert result["status"] == UNKNOWN
    assert "fanduel" in result["missing_books"]


def test_arizona_pack_lists_licensed_books_and_reviewed_coverage():
    pack = pack_summary("az")
    assert pack["ok"] is True
    assert "draftkings" in pack["licensed_books"]
    assert "fanduel" in pack["reviewed_books"]
    assert "betmgm" in pack["missing_high_volume_books"]


def test_florida_pack_is_hard_rock_monopoly_hint():
    assert licensed_books("fl") == ["hardrockbet"]
    assert "monopoly" in pack_summary("fl")["notes"].lower()


def test_packs_http_route():
    listing = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/packs"}, None)
    body = json.loads(listing["body"])
    assert listing["statusCode"] == 200
    assert body["count"] >= 30
    az = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/packs/az"}, None)
    az_body = json.loads(az["body"])
    assert az["statusCode"] == 200
    assert az_body["state"] == "az"
    assert az_body["places_bets"] is False


def test_quote_store_roundtrip_and_stale_miss():
    reset_memory()
    put_snapshot("baseball_mlb", [{"id": "e1", "quotes": []}], meta={"ok": True, "regions": "us,us2"}, now_ms=1)
    stale = get_snapshot("baseball_mlb", max_age_seconds=1)
    assert stale and stale["ok"] is False and stale["stale"] is True
    put_snapshot("baseball_mlb", [{"id": "e1", "event": "A @ B", "quotes": []}], meta={"ok": True})
    fresh = get_snapshot("baseball_mlb", max_age_seconds=120)
    assert fresh and fresh["ok"] is True
    assert fresh["events"][0]["id"] == "e1"
    reset_memory()


def test_scan_reads_store_without_live_provider(monkeypatch):
    reset_memory()
    put_snapshot("baseball_mlb", [{
        "id": "stored", "event_id": "stored", "event": "A @ B", "sport": "baseball_mlb",
        "market": "h2h", "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "draftkings", "outcome": "A", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2, "last_update": fresh_ts()},
        ],
    }], meta={"ok": True})

    def boom(*args, **kwargs):
        raise AssertionError("live provider should not run")

    monkeypatch.setattr("app.scan_sport_payload", boom)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "jurisdiction": "az", "source": "store"},
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "store"
    assert body["n_arbs"] == 1
    assert body["pack"]["state"] == "az"
    reset_memory()


def test_collector_rotates_sports(monkeypatch):
    reset_memory()
    monkeypatch.setattr("collector.list_sports", lambda all_sports=False: (
        [{"key": "baseball_mlb", "active": True}, {"key": "basketball_nba", "active": True}],
        {"ok": True},
    ))

    def fake_scan(sport, **kwargs):
        return {"events": [{"id": sport, "quotes": []}], "status": {"ok": True, "sport": sport, "regions": kwargs.get("regions")}}

    monkeypatch.setattr("collector.scan_sport_payload", fake_scan)
    monkeypatch.setenv("ARB_COLLECT_MAX_SPORTS", "1")
    first = collect_tick()
    second = collect_tick()
    assert first["n_sports"] == 1 and second["n_sports"] == 1
    assert first["sports"][0]["sport"] != second["sports"][0]["sport"]
    snap = get_snapshot(first["sports"][0]["sport"], max_age_seconds=120)
    assert snap and snap["ok"] is True
    reset_memory()
