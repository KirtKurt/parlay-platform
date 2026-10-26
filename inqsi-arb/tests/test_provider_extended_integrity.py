"""Extended-market provider responses must not misstate event/sport coverage."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


def _game(event_id="event-1", sport_key="baseball_mlb"):
    return {
        "id": event_id, "sport_key": sport_key,
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [{
            "key": "book-one",
            "markets": [{"key": "player_points", "outcomes": [
                {"name": "Over", "description": "Player", "point": 12.5, "price": -110},
                {"name": "Under", "description": "Player", "point": 12.5, "price": +110},
            ]}],
        }],
    }


def _fetch(monkeypatch, catalog, odds_by_event=None, max_events=100):
    calls = []
    odds_by_event = odds_by_event or {}

    def fake_get(url, params):
        calls.append(url)
        if url.endswith("/events/"):
            return catalog, {"ok": True, "status": 200}
        event_id = url.split("/events/", 1)[1].split("/", 1)[0]
        return odds_by_event.get(event_id, _game(event_id)), {"ok": True, "status": 200}

    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", fake_get)
    rows, meta = provider.fetch_event_markets(
        "baseball_mlb", ["player_points"], regions="us", max_events=max_events,
    )
    return rows, meta, calls


def test_malformed_extended_event_catalog_fails_closed_without_event_probes(monkeypatch):
    rows, meta, calls = _fetch(monkeypatch, {"unexpected": "object"})
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["events"]["error"] == "MALFORMED_PROVIDER_EVENT_CATALOG"
    assert len(calls) == 1


def test_extended_catalog_preserves_valid_peer_and_skips_invalid_sports(monkeypatch):
    rows, meta, calls = _fetch(monkeypatch, [
        {"id": "event-1", "sport_key": "baseball_mlb"},
        {"id": "other-sport", "sport_key": "basketball_nba"},
        {"sport_key": "baseball_mlb"},
    ])
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_events_invalid"] == 2
    assert len(calls) == 2  # catalog and one correctly scoped event odds request
    assert {error["error"] for error in meta["errors"]} == {
        "MISMATCHED_PROVIDER_SPORT_KEY", "MALFORMED_PROVIDER_EVENT_CATALOG_RECORD",
    }


def test_extended_odds_wrong_event_or_sport_cannot_contaminate_valid_peer(monkeypatch):
    rows, meta, calls = _fetch(monkeypatch, [
        {"id": "event-1"}, {"id": "event-2"}, {"id": "event-3"},
    ], {
        "event-1": _game("event-1"),
        "event-2": _game("different-event"),
        "event-3": _game("event-3", sport_key="basketball_nba"),
    })
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert len(calls) == 4
    assert [error["error"] for error in meta["errors"]] == [
        "MISMATCHED_PROVIDER_EVENT_ID", "MISMATCHED_PROVIDER_SPORT_KEY",
    ]


def test_extended_odds_malformed_nested_response_is_not_valid_coverage(monkeypatch):
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {
        "event-1": {"id": "event-1", "bookmakers": [{"key": "book-one", "markets": "invalid"}]},
    })
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["errors"][0]["error"] == "MALFORMED_PROVIDER_EVENT_ODDS"
    assert len(calls) == 2


def test_extended_odds_without_usable_quotes_and_event_cap_mark_partial(monkeypatch):
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {
        "event-1": {"id": "event-1", "bookmakers": []},
    })
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["errors"][0]["error"] == "NO_USABLE_PROVIDER_MARKET_ODDS"
    assert len(calls) == 2

    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}, {"id": "event-2"}], max_events=1)
    assert len(rows) == 1
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_events_requested"] == 1
    assert len(calls) == 2


def test_extended_ignores_unsolicited_market_but_keeps_requested_quotes(monkeypatch):
    game = _game()
    game["bookmakers"][0]["markets"].append({
        "key": "player_assists",
        "outcomes": [
            {"name": "Over", "description": "Player", "point": 4.5, "price": -110},
            {"name": "Under", "description": "Player", "point": 4.5, "price": +110},
        ],
    })
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {"event-1": game})
    assert len(rows) == 1
    assert rows[0]["market"] == "player_points"
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["errors"][0]["error"] == "UNREQUESTED_PROVIDER_MARKET_ODDS"
    assert len(calls) == 2


def test_extended_only_unsolicited_market_fails_closed(monkeypatch):
    game = _game()
    game["bookmakers"][0]["markets"][0]["key"] = "player_assists"
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {"event-1": game})
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["errors"][0]["error"] == "UNREQUESTED_PROVIDER_MARKET_ODDS"
    assert len(calls) == 2


def test_extended_invalid_explicit_market_points_keep_valid_peers_and_mark_partial(monkeypatch):
    game = _game()
    game["bookmakers"][0]["markets"][0]["outcomes"].extend([
        {"name": "Over", "description": "Player", "point": "nan", "price": 125},
        {"name": "Under", "description": "Player", "point": float("inf"), "price": -125},
    ])
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {"event-1": game})
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_outcomes_invalid_point"] == 2
    assert meta["errors"][0]["error"] == "INVALID_PROVIDER_MARKET_POINT"
    assert meta["errors"][0]["n_outcomes_invalid_point"] == 2
    assert len(calls) == 2


def test_extended_all_invalid_points_cannot_be_reported_as_complete(monkeypatch):
    game = _game()
    game["bookmakers"][0]["markets"][0]["outcomes"] = [
        {"name": "Over", "description": "Player", "point": "invalid", "price": -110},
        {"name": "Under", "description": "Player", "point": float("-inf"), "price": 110},
    ]
    rows, meta, calls = _fetch(monkeypatch, [{"id": "event-1"}], {"event-1": game})
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_outcomes_invalid_point"] == 2
    assert meta["errors"][0]["error"] == "INVALID_PROVIDER_MARKET_POINT"
    assert len(calls) == 2
