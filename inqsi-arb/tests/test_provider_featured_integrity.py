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


def test_cross_sport_featured_odds_are_excluded_without_losing_valid_peer(monkeypatch):
    correct = _valid_game()
    correct["sport_key"] = "baseball_mlb"
    wrong = _valid_game()
    wrong["id"] = "event-other"
    wrong["sport_key"] = "basketball_nba"
    rows, meta = _fetch(monkeypatch, [correct, wrong])
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert rows[0]["sport"] == "baseball_mlb"
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_games_wrong_sport"] == 1
    assert meta["n_games_invalid"] == 1
    assert meta["error"] == "MISMATCHED_PROVIDER_SPORT_KEY"


def test_unpriced_featured_event_does_not_certify_complete_coverage(monkeypatch):
    valid = _valid_game()
    unpriced = _valid_game()
    unpriced["id"] = "event-unpriced"
    unpriced["bookmakers"][0]["markets"][0]["outcomes"] = [
        {"name": "Home", "price": float("nan")},
        {"name": "Away", "price": 1.91},
    ]
    rows, meta = _fetch(monkeypatch, [valid, unpriced])
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_games_unpriced"] == 1
    assert meta["error"] == "NO_USABLE_PROVIDER_FEATURED_ODDS"


def test_all_unpriced_featured_events_fail_closed_not_empty_slate(monkeypatch):
    unpriced = _valid_game()
    unpriced["bookmakers"] = []
    rows, meta = _fetch(monkeypatch, [unpriced])
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_games_unpriced"] == 1
    assert meta["error"] == "NO_USABLE_PROVIDER_FEATURED_ODDS"
