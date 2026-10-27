"""Fail-closed coverage evidence for duplicate live market inventory identities."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import market_discovery as discovery


def _inventory(monkeypatch, books):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery, "_get",
        lambda *_args, **_kwargs: (
            {"id": "event-1", "sport_key": "baseball_mlb", "bookmakers": books},
            {"ok": True, "status": 200},
        ),
    )


def test_duplicate_bookmaker_key_cannot_certify_live_market_coverage(monkeypatch):
    _inventory(monkeypatch, [
        {"key": "book-a", "markets": [{"key": "h2h"}]},
        {"key": "book-a", "markets": [{"key": "totals"}]},
    ])
    keys, meta = discovery._discover_event_markets_direct(
        "baseball_mlb", "event-1", regions="us",
    )
    assert keys == []
    assert meta["ok"] is False
    assert meta["error"] == "DUPLICATE_PROVIDER_BOOKMAKER_KEY"


def test_duplicate_market_key_within_book_cannot_certify_coverage(monkeypatch):
    _inventory(monkeypatch, [
        {"key": "book-a", "markets": [{"key": "h2h"}, {"key": "h2h"}]},
    ])
    keys, meta = discovery._discover_event_markets_direct(
        "baseball_mlb", "event-1", regions="us",
    )
    assert keys == []
    assert meta["ok"] is False
    assert meta["error"] == "DUPLICATE_PROVIDER_MARKET_KEY"


def test_shared_market_across_distinct_books_remains_valid(monkeypatch):
    _inventory(monkeypatch, [
        {"key": "book-a", "markets": [{"key": "h2h"}, {"key": "totals"}]},
        {"key": "book-b", "markets": [{"key": "h2h"}]},
    ])
    keys, meta = discovery._discover_event_markets_direct(
        "baseball_mlb", "event-1", regions="us",
    )
    assert keys == ["h2h", "totals"]
    assert meta["ok"] is True


def test_duplicate_inventory_uses_bounded_partial_fallback(monkeypatch):
    _inventory(monkeypatch, [
        {"key": "book-a", "markets": [{"key": "h2h"}, {"key": "h2h"}]},
    ])
    monkeypatch.setattr(discovery, "candidate_markets_for_sport", lambda _sport: ["h2h"])
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [], [{"ok": True, "status": 200, "error": None}],
        ),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-1")
    assert keys == ["h2h"]
    assert meta["fallback_used"] is True
    assert meta["partial"] is True
    assert meta["event_markets_error"] == "DUPLICATE_PROVIDER_MARKET_KEY"
