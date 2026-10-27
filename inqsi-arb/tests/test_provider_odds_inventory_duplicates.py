"""Provider odds identity integrity: never certify duplicated live inventories."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


def _payload(bookmakers):
    return {
        "id": "event-duplicate-odds",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": bookmakers,
    }


def _book(key, markets=None):
    return {
        "key": key,
        "markets": markets if markets is not None else [
            {"key": "h2h", "outcomes": [
                {"name": "Home", "price": -110},
                {"name": "Away", "price": 110},
            ]},
        ],
    }


def _probe(monkeypatch, books):
    monkeypatch.setattr(
        discovery, "_request_event_odds",
        lambda *_args, **_kwargs: (
            _payload(books), {"ok": True, "status": 200},
        ),
    )
    return discovery._probe_market_batch(
        "baseball_mlb", "event-duplicate-odds", ["h2h"],
        regions="us", bookmakers=None,
    )


def test_duplicate_bookmaker_in_live_odds_keeps_quotes_but_marks_partial(monkeypatch):
    accepted, rows, evidence = _probe(monkeypatch, [
        _book("book-a"), _book("book-a"),
    ])
    assert accepted == ["h2h"]
    assert rows
    assert evidence[0]["ok"] is False
    assert evidence[0]["error"] == "DUPLICATE_PROVIDER_BOOKMAKER_ODDS_KEY"
    assert evidence[0]["n_bookmakers_duplicate_identity"] == 1
    assert discovery._primary_probe_failure(
        evidence, terminal_only=True,
    )["error"] == "DUPLICATE_PROVIDER_BOOKMAKER_ODDS_KEY"


def test_duplicate_market_within_book_keeps_quotes_but_marks_partial(monkeypatch):
    repeated = _book("book-a")["markets"][0]
    accepted, rows, evidence = _probe(monkeypatch, [
        _book("book-a", [repeated, dict(repeated)]),
    ])
    assert accepted == ["h2h"]
    assert rows
    assert evidence[0]["ok"] is False
    assert evidence[0]["error"] == "DUPLICATE_PROVIDER_MARKET_ODDS_KEY"
    assert evidence[0]["n_markets_duplicate_identity"] == 1
    assert discovery._primary_probe_failure(
        evidence, terminal_only=True,
    )["error"] == "DUPLICATE_PROVIDER_MARKET_ODDS_KEY"


def test_same_market_at_distinct_books_remains_complete(monkeypatch):
    accepted, rows, evidence = _probe(monkeypatch, [
        _book("book-a"), _book("book-b"),
    ])
    assert accepted == ["h2h"]
    assert rows
    assert evidence[0]["ok"] is True
    assert evidence[0].get("n_bookmakers_duplicate_identity") is None
    assert evidence[0].get("n_markets_duplicate_identity") is None
