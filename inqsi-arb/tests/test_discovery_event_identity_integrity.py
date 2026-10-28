"""Event-market summaries must not probe malformed or cross-sport event IDs."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


def test_event_market_summary_rejects_invalid_and_cross_sport_ids(monkeypatch):
    calls = []

    def fake_discover(sport, event_id, **kwargs):
        calls.append((sport, event_id))
        return ["h2h"], {"ok": True, "partial": False, "fallback_used": False}

    monkeypatch.setattr(discovery, "discover_event_market_keys", fake_discover)
    events = [
        {"id": "valid-1", "sport_key": "baseball_mlb"},
        {"id": True},
        {"id": 123},
        {"id": []},
        {"id": " "},
        {"id": " padded "},
        {"id": "wrong-sport", "sport_key": "basketball_nba"},
        {"id": "valid-2"},  # Legacy provider event without an explicit sport key.
    ]
    report = discovery.discover_sport_event_markets(
        "baseball_mlb", events, max_events=20,
    )
    assert calls == [("baseball_mlb", "valid-1"), ("baseball_mlb", "valid-2")]
    assert report["events"] == {"valid-1": ["h2h"], "valid-2": ["h2h"]}
    assert report["n_events"] == 2
    assert report["n_events_requested"] == len(events)
    assert report["partial"] is True
    assert [row["error"] for row in report["errors"]].count(
        "MALFORMED_PROVIDER_EVENT_CATALOG_RECORD"
    ) == 5
    assert any(row == {
        "event_id": "wrong-sport", "error": "MISMATCHED_PROVIDER_SPORT_KEY"
    } for row in report["errors"])


def test_event_market_summary_deduplicates_provider_event_ids(monkeypatch):
    calls = []

    def fake_discover(sport, event_id, **kwargs):
        calls.append(event_id)
        return ["h2h"], {"ok": True, "partial": False, "fallback_used": False}

    monkeypatch.setattr(discovery, "discover_event_market_keys", fake_discover)
    report = discovery.discover_sport_event_markets(
        "baseball_mlb", [{"id": "one"}, {"id": "one"}, {"id": "two"}],
    )
    assert calls == ["one", "two"]
    assert report["events"] == {"one": ["h2h"], "two": ["h2h"]}
    assert report["partial"] is True
    assert report["errors"] == [
        {"event_id": "one", "error": "DUPLICATE_PROVIDER_EVENT_ID"}
    ]
