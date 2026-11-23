"""Provider bookmaker identity integrity must not overstate live coverage."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


def _book(market="h2h", *, key="book-one", title=None):
    book = {
        "markets": [{"key": market, "outcomes": [
            {"name": "Home", "price": -110},
            {"name": "Away", "price": 110},
        ]}],
    }
    if key is not None:
        book["key"] = key
    if title is not None:
        book["title"] = title
    return book


def _game(market="h2h"):
    return {
        "id": "event-1", "sport_key": "baseball_mlb",
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [_book(market)],
    }


def test_featured_title_only_book_keeps_quotes_but_cannot_certify_coverage(monkeypatch):
    game = _game()
    game["bookmakers"].append(_book(key=None, title="Unkeyed Sportsbook"))
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *_args, **_kwargs: (
        [game], {"ok": True, "status": 200},
    ))
    rows, meta = provider.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert len(rows) == 1
    assert {q["book"] for q in rows[0]["quotes"]} == {"book-one", "unkeyed sportsbook"}
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_bookmakers_invalid_identity"] == 1
    assert meta["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"


def test_extended_mixed_unkeyed_book_keeps_valid_prices_and_marks_partial(monkeypatch):
    game = _game("player_points")
    game["bookmakers"].append(_book("player_points", key=None, title="Title Only"))
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")

    def fake_get(url, _params):
        if url.endswith("/events/"):
            return [{"id": "event-1"}], {"ok": True, "status": 200}
        return game, {"ok": True, "status": 200}

    monkeypatch.setattr(provider, "_get", fake_get)
    rows, meta = provider.fetch_event_markets(
        "baseball_mlb", ["player_points"], regions="us",
    )
    assert len(rows) == 1
    assert {q["book"] for q in rows[0]["quotes"]} == {"book-one", "title only"}
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_bookmakers_invalid_identity"] == 1
    assert any(error["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"
               and error["n_bookmakers_invalid_identity"] == 1
               for error in meta["errors"])


def test_featured_all_unidentified_books_fail_closed(monkeypatch):
    game = _game()
    game["bookmakers"] = [_book(key=None)]
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *_args, **_kwargs: (
        [game], {"ok": True, "status": 200},
    ))
    rows, meta = provider.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_bookmakers_invalid_identity"] == 1


def test_featured_valid_bookmaker_identity_stays_complete(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *_args, **_kwargs: (
        [_game()], {"ok": True, "status": 200},
    ))
    rows, meta = provider.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert len(rows) == 1
    assert meta["ok"] is True
    assert meta["partial"] is False
    assert meta["n_bookmakers_invalid_identity"] == 0


def test_featured_padded_bookmaker_key_preserves_quotes_but_marks_partial(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    for padded_key in (" book-two", "book-two ", " book-two "):
        game = _game()
        game["bookmakers"].append(_book(key=padded_key))
        monkeypatch.setattr(provider, "_get", lambda *_args, _game=game, **_kwargs: (
            [_game], {"ok": True, "status": 200},
        ))
        rows, meta = provider.fetch_featured_odds(
            "baseball_mlb", markets=["h2h"], regions="us",
        )
        assert len(rows) == 1
        assert {q["book"] for q in rows[0]["quotes"]} == {"book-one", "book-two"}
        assert meta["ok"] is True
        assert meta["partial"] is True
        assert meta["n_bookmakers_invalid_identity"] == 1
        assert meta["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"


def test_extended_padded_bookmaker_key_preserves_quotes_but_marks_partial(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    for padded_key in (" book-two", "book-two ", " book-two "):
        game = _game("player_points")
        game["bookmakers"].append(_book("player_points", key=padded_key))

        def fake_get(url, _params):
            if url.endswith("/events/"):
                return [{"id": "event-1"}], {"ok": True, "status": 200}
            return game, {"ok": True, "status": 200}

        monkeypatch.setattr(provider, "_get", fake_get)
        rows, meta = provider.fetch_event_markets(
            "baseball_mlb", ["player_points"], regions="us",
        )
        assert len(rows) == 1
        assert {q["book"] for q in rows[0]["quotes"]} == {"book-one", "book-two"}
        assert meta["ok"] is True
        assert meta["partial"] is True
        assert meta["n_bookmakers_invalid_identity"] == 1
        assert any(
            error["error"] == "INVALID_PROVIDER_BOOKMAKER_IDENTITY"
            and error["n_bookmakers_invalid_identity"] == 1
            for error in meta["errors"]
        )
