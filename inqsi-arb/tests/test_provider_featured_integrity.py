"""Featured provider HTTP-200 schema integrity: retain valid prices, report partial."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


def _fetch(monkeypatch, payload):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: (payload, {"ok": True, "status": 200}))
    return provider.fetch_featured_odds("baseball_mlb", markets=["h2h"], regions="us")


def _valid_game():
    return {
        "id": "event-1", "home_team": "Home", "away_team": "Away",
        "bookmakers": [{
            "key": "book-one",
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": -110},
                {"name": "Away", "price": +110},
            ]}],
        }],
    }


def test_malformed_top_level_featured_response_is_not_valid_empty_slate(monkeypatch):
    rows, meta = _fetch(monkeypatch, {"unexpected": "object"})
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["error"] == "MALFORMED_PROVIDER_FEATURED_ODDS"


def test_malformed_nested_featured_record_retains_valid_peer_and_marks_partial(monkeypatch):
    rows, meta = _fetch(monkeypatch, [_valid_game(), {"id": "event-2", "bookmakers": [{"markets": "bad"}]}])
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_games_received"] == 2
    assert meta["n_games_invalid"] == 1
    assert meta["error"] == "MALFORMED_PROVIDER_FEATURED_ODDS"


def test_all_malformed_featured_records_fail_closed(monkeypatch):
    rows, meta = _fetch(monkeypatch, [{"id": "event-2", "bookmakers": [{"markets": [{"key": "h2h", "outcomes": "bad"}]}]}])
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_games_invalid"] == 1


def test_legitimate_empty_featured_slate_is_not_malformed(monkeypatch):
    rows, meta = _fetch(monkeypatch, [])
    assert rows == []
    assert meta["ok"] is True
    assert meta["partial"] is False
    assert meta["n_games_received"] == 0
    assert meta["n_games_invalid"] == 0
