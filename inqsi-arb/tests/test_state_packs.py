import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation  # noqa: F401  registers state packs
from app import lambda_handler
from collector import collect_tick
from quote_store import get_snapshot, put_checkpoint, put_snapshot, reset_memory
from rules import COMPATIBLE, UNKNOWN, compatibility, lookup
from state_packs import licensed_books, pack_summary


def fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def future_ts():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_arizona_baseball_cross_book_is_compatible_after_house_rule_review():
    assert lookup("fanduel", "baseball", "winner", "az") is not None
    result = compatibility(["draftkings", "fanduel"], "baseball", "winner", "az")
    assert result["status"] == COMPATIBLE
    assert result["settlement_profile"] == "mlb_full_game_2way_action_v1"


def test_unread_fanduel_state_still_fails_closed():
    result = compatibility(["draftkings", "fanduel"], "baseball", "winner", "wy")
    assert result["status"] == UNKNOWN
    assert "fanduel" in result["missing_books"]


def test_arizona_pack_lists_licensed_books_and_reviewed_coverage():
    pack = pack_summary("az")
    assert pack["ok"] is True
    assert "draftkings" in pack["licensed_books"]
    assert "fanduel" in pack["reviewed_books"]
    assert "betmgm" in pack["missing_high_volume_books"]


def test_direct_state_pack_import_bootstraps_supplemental_rules():
    script = (
        "import json; from state_packs import pack_summary; "
        "print(json.dumps(pack_summary('az')['reviewed_books']))"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    reviewed = set(json.loads(subprocess.check_output(
        [sys.executable, "-c", script], env=env, text=True,
    )))
    assert {"draftkings", "fanduel", "fanatics", "williamhill_us"} <= reviewed


def test_florida_pack_is_hard_rock_monopoly_hint():
    assert set(licensed_books("fl")) == {"hardrockbet", "hardrockbet_fl"}
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
        "market": "h2h", "commence_time": future_ts(), "expected_outcomes": ["A", "B"],
        "quotes": [
            {"book": "draftkings", "outcome": "A", "decimal": 2.2, "last_update": fresh_ts()},
            {"book": "fanduel", "outcome": "B", "decimal": 2.2, "last_update": fresh_ts()},
        ],
    }], meta={"ok": True, "markets": ["h2h"], "regions": "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"})

    def boom(*args, **kwargs):
        raise AssertionError("live provider should not run")

    monkeypatch.setattr("app.scan_sport_payload", boom)
    response = lambda_handler({
        "httpMethod": "GET",
        "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "source": "store"},
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "store"
    assert "pack" not in body
    assert body["product_filter"] == "books"
    assert body["books"] is None
    assert body["n_arbs"] == 0
    assert body["n_held_unverified"] >= 1
    assert body["n_detected_unverified"] == 1
    assert body["detected_unverified"][0]["validation"]["qualification_reason"] == "SETTLEMENT_RULES_NOT_VERIFIED_COMPATIBLE"
    reset_memory()


def test_failed_provider_snapshot_is_not_returned_as_success():
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": False, "error": "provider down"})
    snap = get_snapshot("baseball_mlb", max_age_seconds=120)
    assert snap and snap["ok"] is False and snap["incomplete"] is True
    reset_memory()


def test_collector_does_not_replace_successful_head_on_provider_failure(monkeypatch):
    reset_memory()
    put_snapshot("baseball_mlb", [{"id": "known-good"}], meta={"ok": True})
    before = get_snapshot("baseball_mlb", max_age_seconds=120)
    monkeypatch.setattr("collector.scan_sport_payload", lambda *args, **kwargs: {
        "events": [], "status": {"ok": False, "error": "provider down"},
    })
    from collector import collect_sport
    result = collect_sport("baseball_mlb")
    after = get_snapshot("baseball_mlb", max_age_seconds=120)
    assert result["ok"] is False
    assert after and after["events"] == before["events"]
    assert after["head"]["version"] == before["head"]["version"]
    reset_memory()


def test_store_scan_rejects_event_at_or_after_commencement(monkeypatch):
    reset_memory()
    now = datetime.now(timezone.utc)
    put_snapshot("baseball_mlb", [{
        "id": "started", "event": "A @ B", "sport": "baseball_mlb", "market": "h2h",
        "commence_time": now.isoformat(), "quotes": [],
    }], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    monkeypatch.setattr("app.datetime", type("Clock", (), {"now": staticmethod(lambda tz=None: now), "fromisoformat": staticmethod(datetime.fromisoformat)}))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "regions": "us", "source": "store"},
    }, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["n_markets"] == 0
    reset_memory()


def test_snapshot_rejects_individually_oversized_event_before_writes():
    reset_memory()
    with pytest.raises(ValueError, match="safety budget"):
        put_snapshot("baseball_mlb", [{"id": "huge", "quotes": [{"blob": "x" * 400_000}]}])
    assert get_snapshot("baseball_mlb", max_age_seconds=120) is None


def test_store_scan_requires_requested_market_and_region_dimensions(monkeypatch):
    reset_memory()
    put_snapshot("baseball_mlb", [
        {"id": "h", "sport": "baseball_mlb", "market": "h2h", "quotes": []},
        {"id": "t", "sport": "baseball_mlb", "market": "totals", "quotes": []},
    ], meta={"ok": True, "markets": ["h2h", "totals"], "regions": "us,us2"})
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live provider should not run")))
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan", "queryStringParameters": {
        "sport": "baseball_mlb", "markets": "h2h", "regions": "us,us2", "source": "store",
    }}, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["n_markets"] == 0
    mismatch = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan", "queryStringParameters": {
        "sport": "baseball_mlb", "markets": "spreads", "regions": "eu", "source": "store",
    }}, None)
    assert mismatch["statusCode"] == 503
    assert json.loads(mismatch["body"])["error"] == "QUOTE_SNAPSHOT_REGION_MISMATCH"
    subset = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan", "queryStringParameters": {
        "sport": "baseball_mlb", "markets": "h2h", "regions": "us", "source": "store",
    }}, None)
    assert subset["statusCode"] == 503
    assert json.loads(subset["body"])["error"] == "QUOTE_SNAPSHOT_REGION_MISMATCH"
    reset_memory()


def test_store_all_sports_uses_only_persisted_inventory(monkeypatch):
    reset_memory()
    put_snapshot("baseball_mlb", [], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    monkeypatch.setattr("app.list_sports", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("catalog should not run")))
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live provider should not run")))
    response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan", "queryStringParameters": {
        "sport": "all", "markets": "h2h", "regions": "us", "source": "store",
    }}, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"]["n_sports_scanned"] == 1
    reset_memory()


def test_book_first_child_ignores_legacy_licensed_filter(monkeypatch):
    observed = []
    def fake_scan(sport, **kwargs):
        observed.append(kwargs.get("bookmakers"))
        return {"events": [], "status": {"ok": True}}
    monkeypatch.setattr("app.scan_sport_payload", fake_scan)
    for jurisdiction in ("az", "ri"):
        response = lambda_handler({"httpMethod": "GET", "path": "/v1/arb/scan", "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "source": "live", "licensed": "true",
            "jurisdiction": jurisdiction, "books": "draftkings,unlicensed",
        }}, None)
        assert response["statusCode"] == 200
    assert observed == ["draftkings,unlicensed", "draftkings,unlicensed"]


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


def test_collector_isolates_one_sport_failure_and_advances(monkeypatch):
    reset_memory()
    monkeypatch.setattr("collector.list_sports", lambda all_sports=False: ([
        {"key": "bad", "active": True}, {"key": "good", "active": True},
    ], {"ok": True}))
    monkeypatch.setenv("ARB_COLLECT_MAX_SPORTS", "2")
    def fake_collect(sport):
        if sport == "bad":
            raise ValueError("oversized event")
        return {"sport": sport, "ok": True}
    monkeypatch.setattr("collector.collect_sport", fake_collect)
    result = collect_tick()
    assert result["ok"] is True
    assert [row["ok"] for row in result["sports"]] == [False, True]
    assert result["sports"][0]["error"] == "ValueError"
    from quote_store import get_checkpoint
    assert get_checkpoint()["next_index"] == 0
    reset_memory()


def test_snapshot_chunks_receive_expiration_ttl(monkeypatch):
    import quote_store
    reset_memory()
    monkeypatch.setenv("ARB_SNAPSHOT_TTL_SECONDS", "3600")
    put_snapshot("baseball_mlb", [{"id": "e1", "quotes": []}], now_ms=1_000_000)
    chunks = [item for (pk, sk), item in quote_store._MEMORY.items() if sk.startswith("CHUNK#")]
    assert chunks and all(item["ttl"] == 4600 for item in chunks)
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    state_section = template.split("ArbStateTable:", 1)[1].split("ArbConnectionsTable:", 1)[0]
    assert "TimeToLiveSpecification:" in state_section
    assert "AttributeName: ttl" in state_section


def test_auto_all_reuses_catalog_when_snapshot_falls_back_live(monkeypatch):
    reset_memory()
    calls = 0
    def fake_list(all_sports=False):
        nonlocal calls
        calls += 1
        return ([{"key": "baseball_mlb", "active": True}], {"ok": True})
    monkeypatch.setattr("app.list_sports", fake_list)
    monkeypatch.setattr("app.scan_sport_payload", lambda sport, **kwargs: {
        "events": [], "status": {"ok": True, "sport": sport},
    })
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "all", "markets": "h2h", "source": "auto"},
    }, None)
    assert response["statusCode"] == 200
    assert calls == 1
    reset_memory()


def test_auto_falls_live_when_commencement_filter_empties_snapshot(monkeypatch):
    reset_memory()
    put_snapshot("baseball_mlb", [{
        "id": "started", "sport": "baseball_mlb", "market": "h2h",
        "commence_time": "2000-01-01T00:00:00Z", "quotes": [],
    }], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    calls = []
    monkeypatch.setattr("app.scan_sport_payload", lambda sport, **kwargs: (
        calls.append(sport) or {"events": [], "status": {"ok": True}}
    ))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "regions": "us", "source": "auto",
        },
    }, None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["source"] == "live"
    assert calls == ["baseball_mlb"]
    reset_memory()


def test_auto_cached_all_sports_honors_scan_limit(monkeypatch):
    reset_memory()


def test_auto_scan_accepts_successful_empty_snapshot(monkeypatch):
    reset_memory()
    put_snapshot(
        "baseball_mlb", [],
        meta={"ok": True, "markets": ["h2h"], "regions": "us"},
    )
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(AssertionError("successful empty snapshot must not fan out live")))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "baseball_mlb", "markets": "h2h", "regions": "us",
            "source": "auto",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "store"
    assert body["n_markets"] == 0
    reset_memory()


def test_arizona_basketball_rule_is_scoped_to_reviewed_competitions(monkeypatch):
    monkeypatch.setenv("ARB_MAX_QUOTE_AGE_SECONDS", "3600")
    event = {
        "id": "euroleague", "sport": "basketball_euroleague", "market": "h2h",
        "quotes": [
            {"book": "fanduel", "outcome": "A", "decimal": 2.1, "last_update": fresh_ts()},
            {"book": "fanduel", "outcome": "B", "decimal": 2.1, "last_update": fresh_ts()},
        ],
    }
    rows = validation.validate_event(event, jurisdiction="az")
    assert len(rows) == 1
    assert rows[0]["rules_status"] == "unknown"
    assert rows[0]["context"]["settlement_validation"]["reason"] == "EVENT_OR_MARKET_SCOPE_UNREVIEWED"
    sports = [{"key": f"sport-{index}"} for index in range(3)]
    monkeypatch.setattr("app.list_sports", lambda all_sports=False: (sports, {"ok": True}))
    monkeypatch.setenv("ARB_MAX_SPORTS_PER_ALL_SCAN", "2")
    for row in sports:
        put_snapshot(row["key"], [{
            "id": row["key"], "sport": row["key"], "market": "h2h",
            "commence_time": future_ts(), "quotes": [],
        }], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(AssertionError("live provider should not run")))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "all", "markets": "h2h", "regions": "us", "source": "auto",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["source"] == "store"
    assert body["status"]["n_sports_scanned"] == 2
    reset_memory()


def test_scan_rejects_unknown_source_without_provider_call(monkeypatch):
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (
        _ for _ in ()
    ).throw(AssertionError("live provider should not run")))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {"sport": "baseball_mlb", "markets": "h2h", "source": "strore"},
    }, None)
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"] == "INVALID_SOURCE"


def test_store_all_ignores_retired_snapshot_heads(monkeypatch):
    reset_memory()
    put_snapshot("retired", [], meta={"ok": True, "markets": ["h2h"], "regions": "us"}, now_ms=1)
    put_snapshot("active", [], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    put_checkpoint({"active_sports": ["active"], "next_index": 0})
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live provider should not run")))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "all", "markets": "h2h", "regions": "us", "source": "store",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["status"]["n_sports_scanned"] == 1
    reset_memory()


def test_store_all_reports_missing_active_snapshot_head(monkeypatch):
    reset_memory()
    put_snapshot("active-a", [], meta={"ok": True, "markets": ["h2h"], "regions": "us"})
    put_checkpoint({"active_sports": ["active-a", "active-b"], "next_index": 0})
    monkeypatch.setattr("app.scan_sport_payload", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live provider should not run")))
    response = lambda_handler({
        "httpMethod": "GET", "path": "/v1/arb/scan",
        "queryStringParameters": {
            "sport": "all", "markets": "h2h", "regions": "us", "source": "store",
        },
    }, None)
    body = json.loads(response["body"])
    assert response["statusCode"] == 503
    assert body["error"] == "QUOTE_SNAPSHOT_INCOMPLETE"
    assert [row["sport"] for row in body["sports"]] == ["active-a", "active-b"]
    assert body["sports"][1]["error"] == "QUOTE_SNAPSHOT_MISSING"
    reset_memory()


def test_deployed_snapshot_freshness_covers_full_collector_rotation():
    template = (ROOT / "template.yaml").read_text(encoding="utf-8")
    assert "ARB_COLLECT_MAX_SPORTS: '6'" in template
    assert "ARB_COLLECT_REGIONS: !Ref ArbRegions" in template
    assert "ARB_QUOTE_FRESH_SECONDS: '3600'" in template
    assert "ARB_MAX_QUOTE_AGE_SECONDS: '3600'" in template
    # 100 active sports / 6 per two-minute tick rounds up to 17 ticks.
    assert 3600 >= 17 * 120


def test_collector_regions_follow_default_scan_regions(monkeypatch):
    import collector

    monkeypatch.delenv("ARB_COLLECT_REGIONS", raising=False)
    monkeypatch.setenv("ARB_REGIONS", "us,us2,us_ex,eu")
    monkeypatch.setenv("ARB_US_REGIONS", "us,us2")
    assert collector._regions() == "us,us2,us_ex,eu"


def test_scheduled_handler_raises_when_tick_fully_fails(monkeypatch):
    import collector
    monkeypatch.setattr(collector, "collect_tick", lambda: {
        "ok": False, "error": "SPORT_CATALOG_UNAVAILABLE", "places_bets": False,
    })
    with pytest.raises(RuntimeError, match="SPORT_CATALOG_UNAVAILABLE"):
        collector.handler({}, None)


def test_scheduled_handler_raises_when_tick_partially_fails(monkeypatch):
    import collector
    monkeypatch.setattr(collector, "collect_tick", lambda: {
        "ok": True, "all_ok": False, "n_failed": 1, "places_bets": False,
    })
    with pytest.raises(RuntimeError, match="PARTIAL_SPORT_FAILURE"):
        collector.handler({}, None)
