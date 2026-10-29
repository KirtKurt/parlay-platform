"""Provider odds identity integrity: never certify duplicated live inventories."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery
import provider


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


def test_padded_bookmaker_key_retains_quotes_but_not_complete_coverage(monkeypatch):
    accepted, rows, evidence = _probe(monkeypatch, [_book(" book-a ")])
    assert accepted == ["h2h"]
    assert rows
    assert evidence[0]["ok"] is False
    assert evidence[0]["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"
    assert evidence[0]["n_bookmakers_invalid_identity"] == 1
    assert discovery._primary_probe_failure(
        evidence, terminal_only=True,
    )["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"


def test_padded_market_key_retains_quotes_but_not_complete_coverage(monkeypatch):
    market = _book("book-a")["markets"][0]
    accepted, rows, evidence = _probe(monkeypatch, [
        _book("book-a", [{**market, "key": " h2h "}]),
    ])
    assert accepted == ["h2h"]
    assert rows
    assert evidence[0]["ok"] is False
    assert evidence[0]["error"] == "INVALID_PROVIDER_MARKET_IDENTITY"
    assert evidence[0]["n_markets_invalid_identity"] == 1
    assert discovery._primary_probe_failure(
        evidence, terminal_only=True,
    )["error"] == "INVALID_PROVIDER_MARKET_IDENTITY"


def _featured(monkeypatch, books):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        provider, "_get",
        lambda *_args, **_kwargs: (
            [_payload(books)], {"ok": True, "status": 200},
        ),
    )
    return provider.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )


def _extended(monkeypatch, books):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")

    def fake_get(url, *_args, **_kwargs):
        if url.endswith("/events/"):
            return (
                [{"id": "event-duplicate-odds", "sport_key": "baseball_mlb"}],
                {"ok": True, "status": 200},
            )
        return _payload(books), {"ok": True, "status": 200}

    monkeypatch.setattr(provider, "_get", fake_get)
    return provider.fetch_event_markets(
        "baseball_mlb", ["h2h"], regions="us",
    )


def test_featured_duplicate_bookmaker_retains_quotes_but_marks_partial(monkeypatch):
    rows, meta = _featured(monkeypatch, [_book("book-a"), _book("book-a")])
    assert rows and len(rows[0]["quotes"]) == 4
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "DUPLICATE_PROVIDER_BOOKMAKER_ODDS_KEY"
    assert meta["n_bookmakers_duplicate_identity"] == 1


def test_featured_duplicate_market_retains_quotes_but_marks_partial(monkeypatch):
    market = _book("book-a")["markets"][0]
    rows, meta = _featured(monkeypatch, [
        _book("book-a", [market, dict(market)]),
    ])
    assert rows and len(rows[0]["quotes"]) == 4
    assert meta["partial"] is True
    assert meta["error"] == "DUPLICATE_PROVIDER_MARKET_ODDS_KEY"
    assert meta["n_markets_duplicate_identity"] == 1


def test_extended_duplicate_bookmaker_retains_quotes_but_marks_partial(monkeypatch):
    rows, meta = _extended(monkeypatch, [_book("book-a"), _book("book-a")])
    assert rows and len(rows[0]["quotes"]) == 4
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_bookmakers_duplicate_identity"] == 1
    assert "DUPLICATE_PROVIDER_BOOKMAKER_ODDS_KEY" in {
        error["error"] for error in meta["errors"]
    }


def test_extended_duplicate_market_retains_quotes_but_marks_partial(monkeypatch):
    market = _book("book-a")["markets"][0]
    rows, meta = _extended(monkeypatch, [
        _book("book-a", [market, dict(market)]),
    ])
    assert rows and len(rows[0]["quotes"]) == 4
    assert meta["partial"] is True
    assert meta["n_markets_duplicate_identity"] == 1
    assert "DUPLICATE_PROVIDER_MARKET_ODDS_KEY" in {
        error["error"] for error in meta["errors"]
    }


def test_distinct_books_sharing_market_remain_complete_across_adapters(monkeypatch):
    books = [_book("book-a"), _book("book-b")]
    for fetch in (_featured, _extended):
        rows, meta = fetch(monkeypatch, books)
        assert rows and len(rows[0]["quotes"]) == 4
        assert meta["ok"] is True
        assert meta["partial"] is False
        assert meta["n_bookmakers_duplicate_identity"] == 0
        assert meta["n_markets_duplicate_identity"] == 0
