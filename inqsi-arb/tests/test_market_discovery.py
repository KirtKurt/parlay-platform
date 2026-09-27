import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


def test_event_market_inventory_is_primary_and_not_static_catalog(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")

    def fake_get(url, params):
        assert url.endswith("/sports/baseball_mlb/events/event-1/markets")
        assert params["regions"] == "us,us2"
        return {
            "bookmakers": [
                {"key": "a", "markets": [{"key": "h2h"}, {"key": "batter_hits"}]},
                {"key": "b", "markets": [{"key": "batter_hits"}, {"key": "pitcher_outs"}]},
            ]
        }, {"ok": True, "status": 200}

    monkeypatch.setattr(discovery, "_get", fake_get)
    keys, meta = discovery.discover_event_market_keys(
        "baseball_mlb", "event-1", regions="us,us2", max_markets=250,
    )
    assert keys == ["h2h", "batter_hits", "pitcher_outs"]
    assert meta["discovery"] == "event_markets_endpoint"
    assert meta["fallback_used"] is False
    assert meta["partial"] is False


def test_event_market_inventory_respects_explicit_safety_cap(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery,
        "_get",
        lambda *_args, **_kwargs: (
            {"bookmakers": [{"key": "a", "markets": [{"key": f"m{i}"} for i in range(5)]}]},
            {"ok": True, "status": 200},
        ),
    )
    keys, meta = discovery.discover_event_market_keys(
        "basketball_nba", "event-2", max_markets=3,
    )
    assert keys == ["m0", "m1", "m2"]
    assert meta["partial"] is True


def test_catalog_probe_is_fail_closed_fallback_when_inventory_endpoint_fails(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery,
        "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": 503, "error": "UPSTREAM"}),
    )
    monkeypatch.setattr(
        discovery,
        "_probe_market_batch",
        lambda *_args, **_kwargs: (["h2h"], [], [{"ok": True, "status": 200}]),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-3")
    assert keys == ["h2h"]
    assert meta["fallback_used"] is True
    assert meta["discovery"] == "documented_catalog_runtime_probe_fallback"
    assert meta["event_markets_status"] == 503
