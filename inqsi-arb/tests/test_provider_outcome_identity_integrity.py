"""Malformed provider selection names cannot establish complete ARB coverage."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery
import provider


def _game(market="h2h"):
    return {
        "id": "event-1", "sport_key": "baseball_mlb",
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [{"key": "book-one", "markets": [{
            "key": market,
            "outcomes": [
                {"name": "Home", "price": -110},
                {"name": "Away", "price": 110},
                {"name": None, "price": 200},
                {"name": " ", "price": 200},
                {"name": 7, "price": 200},
                {"name": " Home ", "price": 200},
            ],
        }]}],
    }


def test_featured_invalid_selection_names_retain_good_quotes_but_mark_partial(monkeypatch):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *_a, **_kw: ([_game()], {"ok": True, "status": 200}))
    rows, meta = provider.fetch_featured_odds("baseball_mlb", markets=["h2h"], regions="us")
    assert len(rows) == 1
    assert {q["outcome"] for q in rows[0]["quotes"]} == {"Home", "Away"}
    assert meta["n_outcomes_invalid_identity"] == 4
    assert meta["partial"] is True
    assert meta["error"] == "INVALID_PROVIDER_OUTCOME_IDENTITY"


def test_featured_only_invalid_names_fails_closed(monkeypatch):
    game = _game()
    game["bookmakers"][0]["markets"][0]["outcomes"] = [{"name": None, "price": -110}]
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *_a, **_kw: ([game], {"ok": True, "status": 200}))
    rows, meta = provider.fetch_featured_odds("baseball_mlb", markets=["h2h"], regions="us")
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_outcomes_invalid_identity"] == 1


def test_extended_invalid_selection_names_preserve_good_peer(monkeypatch):
    def fake_get(url, _params):
        if url.endswith("/events/"):
            return [{"id": "event-1", "sport_key": "baseball_mlb"}], {"ok": True, "status": 200}
        return _game("player_points"), {"ok": True, "status": 200}
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", fake_get)
    rows, meta = provider.fetch_event_markets("baseball_mlb", ["player_points"], regions="us")
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert meta["n_outcomes_invalid_identity"] == 4
    assert meta["partial"] is True
    assert "INVALID_PROVIDER_OUTCOME_IDENTITY" in {e["error"] for e in meta["errors"]}


def test_market_probe_retains_valid_prices_but_reports_invalid_selection_identity(monkeypatch):
    monkeypatch.setattr(
        market_discovery, "_request_event_odds",
        lambda *_a, **_kw: (_game(), {"ok": True, "status": 200}),
    )
    keys, rows, evidence = market_discovery._probe_market_batch(
        "baseball_mlb", "event-1", ["h2h"], regions="us", bookmakers=None,
    )
    assert keys == ["h2h"]
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert evidence[0]["ok"] is False
    assert evidence[0]["error"] == "INVALID_PROVIDER_OUTCOME_IDENTITY"
    assert evidence[0]["n_outcomes_invalid_identity"] == 4
