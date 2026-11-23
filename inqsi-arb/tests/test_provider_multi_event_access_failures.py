"""Provider-wide 401/429 failures must not multiply event-market requests.

The full-market scan preserves earlier quotes and explicit incomplete-coverage
evidence without probing remaining events after a provider-wide access failure.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


@pytest.mark.parametrize("status", [401, 429])
def test_full_market_scan_stops_after_provider_wide_access_failure(monkeypatch, status):
    events = [{"id": "one"}, {"id": "two"}, {"id": "three"}]
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (events, {"ok": True, "partial": False}),
    )
    calls = []

    def market_inventory(_sport, event_id, **_kwargs):
        calls.append(event_id)
        return [], {"ok": False, "status": status, "error": f"PROVIDER_HTTP_{status}"}

    monkeypatch.setattr(discovery, "_discover_event_markets_direct", market_inventory)
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Provider-wide denial must not trigger odds probes")
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == []
    assert calls == ["one"]
    assert report["ok"] is False
    assert report["partial"] is True
    assert report["n_events_requested"] == 3
    assert report["n_event_market_requests"] == 1
    assert report["n_events_skipped_access_failure"] == 2
    assert len(report["event_details"]) == 3
    assert report["event_details"][0]["error"] == f"PROVIDER_HTTP_{status}"
    for detail in report["event_details"][1:]:
        assert detail["error"] == "SKIPPED_AFTER_PROVIDER_ACCESS_FAILURE"
        assert detail["event_markets_error"] == f"PROVIDER_HTTP_{status}"
        assert detail["status"] == status
        assert detail["blocking_event_id"] == "one"
        assert detail["probe_requests"] == 0
        assert detail["partial"] is True


def test_full_market_scan_keeps_prior_quotes_when_later_event_is_throttled(monkeypatch):
    events = [{"id": "one"}, {"id": "two"}, {"id": "three"}]
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (events, {"ok": True, "partial": False}),
    )
    calls = []

    def market_inventory(_sport, event_id, **_kwargs):
        calls.append(event_id)
        if event_id == "one":
            return ["h2h"], {"ok": True, "status": 200}
        return [], {"ok": False, "status": 429, "error": "PROVIDER_HTTP_429"}

    monkeypatch.setattr(discovery, "_discover_event_markets_direct", market_inventory)
    quote = {"market": "h2h", "event_id": "one", "quotes": [{"price": 120}]}
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (["h2h"], [quote], [{"ok": True, "status": 200}]),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == [quote]
    assert calls == ["one", "two"]
    assert report["ok"] is True
    assert report["partial"] is True
    assert report["n_events_succeeded"] == 1
    assert report["n_event_market_requests"] == 2
    assert report["n_events_skipped_access_failure"] == 1
    assert report["event_details"][2]["skipped_after_access_failure"] is True


def test_event_specific_403_does_not_suppress_neighboring_events(monkeypatch):
    events = [{"id": "one"}, {"id": "two"}]
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (events, {"ok": True, "partial": False}),
    )
    calls = []

    def market_inventory(_sport, event_id, **_kwargs):
        calls.append(event_id)
        return [], {"ok": False, "status": 403, "error": "PROVIDER_HTTP_403"}

    monkeypatch.setattr(discovery, "_discover_event_markets_direct", market_inventory)
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("403 must not trigger odds fallback")
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == []
    assert calls == ["one", "two"]
    assert report["n_event_market_requests"] == 2
    assert report["n_events_skipped_access_failure"] == 0
    assert report["partial"] is True


# The separate provider.fetch_event_markets adapter must obey the same
# provider-wide failure boundary as the full market-discovery scanner.
import provider


def _extended_event(event_id):
    return {
        "id": event_id,
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [{
            "key": "book-one",
            "markets": [{"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Player", "point": 12.5, "price": -110},
                {"name": "Under", "description": "Player", "point": 12.5, "price": 110},
            ]}],
        }],
    }


@pytest.mark.parametrize("status", [401, 429])
def test_extended_market_scan_stops_after_provider_wide_failure(monkeypatch, status):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    calls = []

    def fake_get(url, _params):
        if url.endswith("/events/"):
            return [{"id": event_id} for event_id in ("one", "two", "three")], {"ok": True, "status": 200}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        calls.append(event_id)
        if event_id == "one":
            return _extended_event(event_id), {"ok": True, "status": 200}
        return None, {"ok": False, "status": status, "error": f"PROVIDER_HTTP_{status}"}

    monkeypatch.setattr(provider, "_get", fake_get)
    rows, report = provider.fetch_event_markets(
        "baseball_mlb", ["player_points"], regions="us"
    )
    assert calls == ["one", "two"]
    assert len(rows) == 1 and rows[0]["event_id"] == "one"
    assert report["ok"] is True
    assert report["partial"] is True
    assert report["n_event_odds_requests"] == 2
    assert report["n_events_skipped_access_failure"] == 1
    assert report["errors"][0]["error"] == f"PROVIDER_HTTP_{status}"
    assert report["errors"][1] == {
        "event_id": "three",
        "error": "SKIPPED_AFTER_PROVIDER_ACCESS_FAILURE",
        "provider_error": f"PROVIDER_HTTP_{status}",
        "status": status,
        "blocking_event_id": "two",
    }


def test_extended_market_scan_first_event_401_fails_closed_without_extra_calls(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    calls = []

    def fake_get(url, _params):
        if url.endswith("/events/"):
            return [{"id": event_id} for event_id in ("one", "two", "three")], {"ok": True}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        calls.append(event_id)
        return None, {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"}

    monkeypatch.setattr(provider, "_get", fake_get)
    rows, report = provider.fetch_event_markets(
        "baseball_mlb", ["player_points"], regions="us"
    )
    assert rows == []
    assert calls == ["one"]
    assert report["ok"] is False
    assert report["partial"] is True
    assert report["n_event_odds_requests"] == 1
    assert report["n_events_skipped_access_failure"] == 2
    assert all(error["error"] == "SKIPPED_AFTER_PROVIDER_ACCESS_FAILURE"
               for error in report["errors"][1:])


def test_extended_market_scan_event_specific_403_checks_neighbors(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    calls = []

    def fake_get(url, _params):
        if url.endswith("/events/"):
            return [{"id": event_id} for event_id in ("one", "two")], {"ok": True}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        calls.append(event_id)
        return None, {"ok": False, "status": 403, "error": "PROVIDER_HTTP_403"}

    monkeypatch.setattr(provider, "_get", fake_get)
    rows, report = provider.fetch_event_markets(
        "baseball_mlb", ["player_points"], regions="us"
    )
    assert rows == []
    assert calls == ["one", "two"]
    assert report["n_event_odds_requests"] == 2
    assert report["n_events_skipped_access_failure"] == 0
    assert report["partial"] is True
