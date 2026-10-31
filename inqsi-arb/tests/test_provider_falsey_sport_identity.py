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


# The standalone provider adapter is also used by the production ARB API.
# Its sport isolation must match the stricter market-discovery path.
import provider as adapter


def _adapter_game(event_id, sport_key="baseball_mlb"):
    game = {
        "id": event_id, "home_team": "Home", "away_team": "Away",
        "bookmakers": [{"key": "book-a", "markets": [{
            "key": "h2h", "outcomes": [
                {"name": "Home", "price": -110},
                {"name": "Away", "price": 110},
            ],
        }]}],
    }
    if sport_key is not ...:
        game["sport_key"] = sport_key
    return game


def test_adapter_featured_rejects_explicit_falsey_sport_key(monkeypatch):
    valid = [_adapter_game("valid"), _adapter_game("legacy", ...)]
    invalid = [
        _adapter_game(f"invalid-{i}", value)
        for i, value in enumerate(("", None, False, [], 0))
    ]
    monkeypatch.setattr(adapter, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        adapter, "_get",
        lambda *_args, **_kwargs: (valid + invalid, {"ok": True, "status": 200}),
    )
    rows, meta = adapter.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert {row["event_id"] for row in rows} == {"valid", "legacy"}
    assert meta["n_games_wrong_sport"] == 5
    assert meta["n_games_invalid"] == 5
    assert meta["partial"] is True
    assert meta["error"] == "MISMATCHED_PROVIDER_SPORT_KEY"


def test_adapter_extended_catalog_skips_explicit_falsey_sport_keys(monkeypatch):
    catalog = [
        {"id": "valid", "sport_key": "baseball_mlb"},
        {"id": "legacy"},
    ] + [
        {"id": f"invalid-{i}", "sport_key": value}
        for i, value in enumerate(("", None, False, [], 0))
    ]
    calls = []

    def fake_get(url, _params):
        calls.append(url)
        if url.endswith("/events/"):
            return catalog, {"ok": True, "status": 200}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        return _adapter_game(event_id), {"ok": True, "status": 200}

    monkeypatch.setattr(adapter, "api_key", lambda: "test-key")
    monkeypatch.setattr(adapter, "_get", fake_get)
    rows, meta = adapter.fetch_event_markets(
        "baseball_mlb", ["h2h"], regions="us",
    )
    assert {row["event_id"] for row in rows} == {"valid", "legacy"}
    assert meta["n_events_invalid"] == 5
    assert meta["partial"] is True
    assert len(calls) == 3  # catalog + only the two valid event probes


def test_adapter_extended_odds_rejects_explicit_falsey_sport_keys(monkeypatch):
    catalog = [{"id": "valid"}] + [
        {"id": f"invalid-{i}"}
        for i in range(5)
    ]
    invalid = ("", None, False, [], 0)

    def fake_get(url, _params):
        if url.endswith("/events/"):
            return catalog, {"ok": True, "status": 200}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        if event_id == "valid":
            return _adapter_game(event_id), {"ok": True, "status": 200}
        return _adapter_game(event_id, invalid[int(event_id.split("-")[1])]), {
            "ok": True, "status": 200,
        }

    monkeypatch.setattr(adapter, "api_key", lambda: "test-key")
    monkeypatch.setattr(adapter, "_get", fake_get)
    rows, meta = adapter.fetch_event_markets(
        "baseball_mlb", ["h2h"], regions="us",
    )
    assert {row["event_id"] for row in rows} == {"valid"}
    assert meta["partial"] is True
    assert len(meta["errors"]) == 5
    assert all(
        error["error"] == "MISMATCHED_PROVIDER_SPORT_KEY"
        for error in meta["errors"]
    )


def test_adapter_featured_rejects_coerced_or_padded_event_ids(monkeypatch):
    # A successful HTTP 200 must not turn booleans, numbers, collections,
    # or whitespace-padded identities into legitimate event market IDs.
    invalid_ids = (None, False, True, 0, 1, [], ["event"], {}, " ", " padded ")
    games = [_adapter_game("valid-event")] + [
        _adapter_game(value) for value in invalid_ids
    ]
    monkeypatch.setattr(adapter, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        adapter, "_get",
        lambda *_args, **_kwargs: (games, {"ok": True, "status": 200}),
    )
    rows, meta = adapter.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert {row["event_id"] for row in rows} == {"valid-event"}
    assert meta["n_games_invalid"] == len(invalid_ids)
    assert meta["partial"] is True
    assert meta["error"] == "MALFORMED_PROVIDER_FEATURED_ODDS"


def test_adapter_extended_catalog_does_not_probe_invalid_event_ids(monkeypatch):
    invalid_ids = (None, False, True, 0, 1, [], ["event"], {}, " ", " padded ")
    catalog = [{"id": "valid-event"}] + [
        {"id": value} for value in invalid_ids
    ]
    calls = []

    def fake_get(url, _params):
        calls.append(url)
        if url.endswith("/events/"):
            return catalog, {"ok": True, "status": 200}
        return _adapter_game("valid-event"), {"ok": True, "status": 200}

    monkeypatch.setattr(adapter, "api_key", lambda: "test-key")
    monkeypatch.setattr(adapter, "_get", fake_get)
    rows, meta = adapter.fetch_event_markets(
        "baseball_mlb", ["h2h"], regions="us",
    )
    assert {row["event_id"] for row in rows} == {"valid-event"}
    assert meta["n_events_invalid"] == len(invalid_ids)
    assert meta["partial"] is True
    assert len(calls) == 2  # catalog and only the valid event
    assert all(
        error["error"] == "MALFORMED_PROVIDER_EVENT_CATALOG_RECORD"
        for error in meta["errors"]
    )
