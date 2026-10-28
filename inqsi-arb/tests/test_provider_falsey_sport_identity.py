"""Explicitly malformed provider sport identities must never prove coverage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import market_discovery as discovery


def test_event_catalog_rejects_falsey_explicit_sport_keys(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    payload = [
        {"id": "valid", "sport_key": "baseball_mlb"},
        {"id": "legacy-missing"},
    ] + [
        {"id": f"invalid-{i}", "sport_key": value}
        for i, value in enumerate(("", None, False, [], 0))
    ]
    monkeypatch.setattr(
        discovery, "_get",
        lambda *_args, **_kwargs: (payload, {"ok": True, "status": 200}),
    )
    events, meta = discovery.discover_events("baseball_mlb")
    assert [event["id"] for event in events] == ["valid", "legacy-missing"]
    assert meta["n_events_invalid"] == 5
    assert meta["partial"] is True


def test_event_market_inventory_rejects_falsey_explicit_sport_keys(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    for value in ("", None, False, [], 0):
        payload = {
            "id": "event-1",
            "sport_key": value,
            "bookmakers": [{"key": "book-a", "markets": [{"key": "h2h"}]}],
        }
        monkeypatch.setattr(
            discovery, "_get",
            lambda *_args, **_kwargs: (payload, {"ok": True, "status": 200}),
        )
        keys, meta = discovery._discover_event_markets_direct(
            "baseball_mlb", "event-1", regions="us",
        )
        assert keys == []
        assert meta["ok"] is False
        assert meta["error"] == "MISMATCHED_PROVIDER_MARKET_INVENTORY_ID"


def test_event_odds_rejects_falsey_explicit_sport_keys(monkeypatch):
    for value in ("", None, False, [], 0):
        payload = {
            "id": "event-1",
            "sport_key": value,
            "home_team": "Home", "away_team": "Away",
            "bookmakers": [{"key": "book-a", "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": "Home", "price": -110},
                    {"name": "Away", "price": 110},
                ],
            }]}],
        }
        monkeypatch.setattr(
            discovery, "_request_event_odds",
            lambda *_args, **_kwargs: (payload, {"ok": True, "status": 200}),
        )
        accepted, rows, evidence = discovery._probe_market_batch(
            "baseball_mlb", "event-1", ["h2h"],
            regions="us", bookmakers=None,
        )
        assert accepted == []
        assert rows == []
        assert evidence[0]["ok"] is False
        assert evidence[0]["error"] == "MISMATCHED_PROVIDER_SPORT_KEY"


def test_event_market_inventory_rejects_explicit_invalid_event_ids(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    for value in ("", None, False, [], 0, "another-event", " event-1 "):
        payload = {
            "id": value, "sport_key": "baseball_mlb",
            "bookmakers": [{"key": "book-a", "markets": [{"key": "h2h"}]}],
        }
        monkeypatch.setattr(
            discovery, "_get",
            lambda *_args, **_kwargs: (payload, {"ok": True, "status": 200}),
        )
        keys, meta = discovery._discover_event_markets_direct(
            "baseball_mlb", "event-1", regions="us",
        )
        assert keys == []
        assert meta["ok"] is False
        assert meta["error"] == "MISMATCHED_PROVIDER_MARKET_INVENTORY_ID"


def test_event_market_inventory_accepts_matching_and_legacy_missing_event_id(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    for payload in (
        {"id": "event-1", "sport_key": "baseball_mlb",
         "bookmakers": [{"key": "book-a", "markets": [{"key": "h2h"}]}]},
        {"sport_key": "baseball_mlb",
         "bookmakers": [{"key": "book-a", "markets": [{"key": "h2h"}]}]},
    ):
        monkeypatch.setattr(
            discovery, "_get",
            lambda *_args, **_kwargs: (payload, {"ok": True, "status": 200}),
        )
        keys, meta = discovery._discover_event_markets_direct(
            "baseball_mlb", "event-1", regions="us",
        )
        assert keys == ["h2h"]
        assert meta["ok"] is True
