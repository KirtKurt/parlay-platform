"""Provider-wide odds-probe access failures stop subsequent event requests.

A successful event-market inventory does not guarantee the event-odds
endpoint can authenticate or stay within quota. Preserve earlier quotes and
evidence without multiplying 401/429 calls across the remaining events.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import market_discovery as discovery


@pytest.mark.parametrize("status", [401, 429])
@pytest.mark.parametrize("retain_quote", [False, True])
def test_odds_probe_global_access_failure_skips_later_events(monkeypatch, status, retain_quote):
    events = [{"id": "event-1"}, {"id": "event-2"}]
    monkeypatch.setattr(discovery, "discover_events",
                        lambda *_args, **_kwargs: (events, {"ok": True, "n_events": 2}))
    inventory_calls = []

    def inventory(_sport, event_id, **_kwargs):
        inventory_calls.append(event_id)
        assert event_id == "event-1", "Later event inventory must not be requested"
        return ["h2h"], {"ok": True, "discovery": "event_markets_endpoint"}

    monkeypatch.setattr(discovery, "_discover_event_markets_direct", inventory)
    quote = {"id": "valid-quote"} if retain_quote else None

    def odds_probe(_sport, event_id, markets, **_kwargs):
        assert event_id == "event-1"
        assert markets == ["h2h"]
        # A 400/422 parent may have produced a valid sibling quote before
        # the 401/429 appeared in a child. Check all evidence, not just row 0.
        return (
            ["h2h"] if retain_quote else [],
            [quote] if retain_quote else [],
            [
                {"ok": False, "status": 422, "error": "UNSUPPORTED_MARKET"},
                {"ok": False, "status": status, "error": f"PROVIDER_HTTP_{status}"},
            ],
        )

    monkeypatch.setattr(discovery, "_probe_market_batch", odds_probe)
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert inventory_calls == ["event-1"]
    assert rows == ([quote] if retain_quote else [])
    assert report["partial"] is True
    assert report["n_events_requested"] == 2
    assert report["n_event_market_requests"] == 1
    assert report["n_events_skipped_access_failure"] == 1
    first, skipped = report["event_details"]
    assert first["status"] == status
    assert first["partial"] is True
    assert skipped["skipped_after_access_failure"] is True
    assert skipped["blocking_event_id"] == "event-1"
    assert skipped["status"] == status
    assert skipped["event_markets_error"] == f"PROVIDER_HTTP_{status}"
    assert skipped["probe_requests"] == 0


def test_event_specific_403_odds_probe_does_not_suppress_other_events(monkeypatch):
    events = [{"id": "event-1"}, {"id": "event-2"}]
    monkeypatch.setattr(discovery, "discover_events",
                        lambda *_args, **_kwargs: (events, {"ok": True, "n_events": 2}))
    calls = []

    def inventory(_sport, event_id, **_kwargs):
        calls.append(event_id)
        return ["h2h"], {"ok": True, "discovery": "event_markets_endpoint"}

    monkeypatch.setattr(discovery, "_discover_event_markets_direct", inventory)

    def odds_probe(_sport, event_id, _markets, **_kwargs):
        if event_id == "event-1":
            return [], [], [{"ok": False, "status": 403, "error": "PROVIDER_HTTP_403"}]
        return ["h2h"], [{"id": "valid-second-event-quote"}], [{"ok": True, "status": 200}]

    monkeypatch.setattr(discovery, "_probe_market_batch", odds_probe)
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert calls == ["event-1", "event-2"]
    assert rows == [{"id": "valid-second-event-quote"}]
    assert report["n_event_market_requests"] == 2
    assert report["n_events_skipped_access_failure"] == 0
    assert report["partial"] is True
