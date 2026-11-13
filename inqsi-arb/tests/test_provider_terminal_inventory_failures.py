"""Do not amplify provider authorization failures or quota throttling.

Static catalog fallback remains available for endpoint-specific failures such
as HTTP 503, but HTTP 403 and 429 cannot be repaired by requesting more odds
with the same credentials/quota.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


@pytest.mark.parametrize("status", [403, 429])
def test_event_market_discovery_does_not_probe_after_terminal_access_failure(monkeypatch, status):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    error = f"PROVIDER_HTTP_{status}"
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": status, "error": error}),
    )

    def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("Terminal provider access failure must not trigger odds requests")

    monkeypatch.setattr(discovery, "_probe_market_batch", unexpected_probe)
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-1")
    assert keys == []
    assert meta["ok"] is False
    assert meta["status"] == status
    assert meta["error"] == error
    assert meta["event_markets_status"] == status
    assert meta["fallback_used"] is False
    assert meta["partial"] is True
    assert meta["probe_requests"] == 0


@pytest.mark.parametrize("status", [403, 429])
def test_full_market_scan_preserves_terminal_failure_without_extra_probes(monkeypatch, status):
    error = f"PROVIDER_HTTP_{status}"
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: ([{"id": "event-1"}], {"ok": True, "n_events": 1}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": status, "error": error}),
    )

    def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("Terminal provider access failure must not trigger odds requests")

    monkeypatch.setattr(discovery, "_probe_market_batch", unexpected_probe)
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == []
    assert report["ok"] is False
    assert report["partial"] is True
    assert report["n_events_succeeded"] == 0
    detail = report["event_details"][0]
    assert detail["error"] == error
    assert detail["status"] == status
    assert detail["event_markets_error"] == error
    assert detail["event_markets_status"] == status
    assert detail["fallback_used"] is False
    assert detail["probe_requests"] == 0
    assert detail["partial"] is True
