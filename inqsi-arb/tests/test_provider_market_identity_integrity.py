"""Provider market keys must be exact before claiming full featured/extended coverage."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


def _game(market="h2h"):
    return {
        "id": "event-1", "sport_key": "baseball_mlb",
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [{
            "key": "book-one",
            "markets": [{"key": market, "outcomes": [
                {"name": "Home", "price": -110},
                {"name": "Away", "price": +110},
            ]}],
        }],
    }


def test_featured_padded_market_key_retains_prices_but_marks_partial(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    for padded in (" h2h", "h2h ", " h2h "):
        game = _game()
        game["bookmakers"][0]["markets"].append({
            "key": padded,
            "outcomes": [{"name": "Home", "price": -105},
                         {"name": "Away", "price": +105}],
        })
        monkeypatch.setattr(provider, "_get", lambda *_a, _g=game, **_kw: (
            [_g], {"ok": True, "status": 200},
        ))
        rows, meta = provider.fetch_featured_odds(
            "baseball_mlb", markets=["h2h"], regions="us",
        )
        assert len(rows) == 1
        assert len(rows[0]["quotes"]) == 4
        assert meta["ok"] is True
        assert meta["partial"] is True
        assert meta["n_markets_invalid_identity"] == 1
        assert meta["error"] == "INVALID_PROVIDER_MARKET_IDENTITY"


def test_extended_padded_market_key_retains_prices_but_marks_partial(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    for padded in (" player_points", "player_points ", " player_points "):
        game = _game("player_points")
        game["bookmakers"][0]["markets"].append({
            "key": padded,
            "outcomes": [{"name": "Home", "price": -105},
                         {"name": "Away", "price": +105}],
        })

        def fake_get(url, _params):
            if url.endswith("/events/"):
                return [{"id": "event-1"}], {"ok": True, "status": 200}
            return game, {"ok": True, "status": 200}

        monkeypatch.setattr(provider, "_get", fake_get)
        rows, meta = provider.fetch_event_markets(
            "baseball_mlb", ["player_points"], regions="us",
        )
        assert len(rows) == 1
        assert len(rows[0]["quotes"]) == 4
        assert meta["ok"] is True
        assert meta["partial"] is True
        assert meta["n_markets_invalid_identity"] == 1
        assert any(
            error["error"] == "INVALID_PROVIDER_MARKET_IDENTITY"
            and error["n_markets_invalid_identity"] == 1
            for error in meta["errors"]
        )


def test_valid_market_keys_remain_complete(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    game = _game()
    monkeypatch.setattr(provider, "_get", lambda *_a, **_kw: (
        [game], {"ok": True, "status": 200},
    ))
    rows, meta = provider.fetch_featured_odds(
        "baseball_mlb", markets=["h2h"], regions="us",
    )
    assert len(rows) == 1
    assert meta["ok"] is True
    assert meta["partial"] is False
    assert meta["n_markets_invalid_identity"] == 0
