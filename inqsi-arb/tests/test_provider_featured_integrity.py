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


def test_featured_ignores_unsolicited_market_but_keeps_requested_quotes(monkeypatch):
    game = _valid_game()
    game["bookmakers"][0]["markets"].append({
        "key": "spreads",
        "outcomes": [
            {"name": "Home", "point": -1.5, "price": +120},
            {"name": "Away", "point": +1.5, "price": -140},
        ],
    })
    rows, meta = _fetch(monkeypatch, [game])
    assert len(rows) == 1
    assert rows[0]["market"] == "h2h"
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "UNREQUESTED_PROVIDER_MARKET_ODDS"
    assert meta["n_market_objects_unrequested"] == 1


def test_featured_only_unsolicited_market_cannot_claim_requested_coverage(monkeypatch):
    game = _valid_game()
    game["bookmakers"][0]["markets"][0]["key"] = "spreads"
    rows, meta = _fetch(monkeypatch, [game])
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["error"] == "UNREQUESTED_PROVIDER_MARKET_ODDS"
    assert meta["n_games_unpriced"] == 1
    assert meta["n_market_objects_unrequested"] == 1


def test_invalid_explicit_market_points_are_excluded_and_coverage_is_partial(monkeypatch):
    game = _valid_game()
    market = game["bookmakers"][0]["markets"][0]
    market["key"] = "spreads"
    market["outcomes"] = [
        {"name": "Home", "point": -1.5, "price": -110},
        {"name": "Away", "point": 1.5, "price": 110},
        {"name": "Home", "point": float("nan"), "price": 125},
        {"name": "Away", "point": float("inf"), "price": -120},
        {"name": "Away", "point": True, "price": 105},
    ]
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: ([game], {"ok": True, "status": 200}))
    rows, meta = provider.fetch_featured_odds("baseball_mlb", markets=["spreads"], regions="us")
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert {quote["point"] for quote in rows[0]["quotes"]} == {-1.5, 1.5}
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "INVALID_PROVIDER_MARKET_POINT"
    assert meta["n_outcomes_invalid_point"] == 3


def test_all_invalid_explicit_market_points_cannot_certify_coverage(monkeypatch):
    game = _valid_game()
    market = game["bookmakers"][0]["markets"][0]
    market["key"] = "totals"
    market["outcomes"] = [
        {"name": "Over", "point": "not-a-number", "price": -110},
        {"name": "Under", "point": float("-inf"), "price": 110},
    ]
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(provider, "_get", lambda *args, **kwargs: ([game], {"ok": True, "status": 200}))
    rows, meta = provider.fetch_featured_odds("baseball_mlb", markets=["totals"], regions="us")
    assert rows == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["error"] == "INVALID_PROVIDER_MARKET_POINT"
    assert meta["n_outcomes_invalid_point"] == 2


def test_featured_mixed_invalid_american_prices_preserve_good_quotes_and_mark_partial(monkeypatch):
    game = _valid_game()
    game["bookmakers"][0]["markets"][0]["outcomes"].extend([
        {"name": "NaN", "price": float("nan")},
        {"name": "Decimal", "price": 1.91},
        {"name": "Boolean", "price": True},
        {"name": "Overflow", "price": 10 ** 400},
    ])
    rows, meta = _fetch(monkeypatch, [game])
    assert len(rows) == 1
    assert len(rows[0]["quotes"]) == 2
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "INVALID_PROVIDER_AMERICAN_ODDS"
    assert meta["n_outcomes_invalid_price"] == 4



def test_sport_scan_marks_partial_when_featured_section_is_partial(monkeypatch):
    # The all-sports endpoint relies on status["partial"], not nested metadata.
    featured = [{"id": "featured-market"}]
    extended = [{"id": "extended-market"}]
    monkeypatch.setattr(provider, "fetch_featured_odds",
                        lambda *args, **kwargs: (featured, {"ok": True, "partial": True}))
    monkeypatch.setattr(provider, "fetch_event_markets",
                        lambda *args, **kwargs: (extended, {"ok": True, "partial": False}))
    result = provider.scan_sport_payload(
        "baseball_mlb", bankroll=100, markets=["h2h", "player_points"], regions="us"
    )
    assert result["events"] == featured + extended
    assert result["status"]["ok"] is True
    assert result["status"]["partial"] is True


def test_sport_scan_marks_partial_when_one_provider_section_fails(monkeypatch):
    featured = [{"id": "featured-market"}]
    monkeypatch.setattr(provider, "fetch_featured_odds",
                        lambda *args, **kwargs: (featured, {"ok": True, "partial": False}))
    monkeypatch.setattr(provider, "fetch_event_markets",
                        lambda *args, **kwargs: ([], {"ok": False, "error": "PROVIDER_HTTP_401"}))
    result = provider.scan_sport_payload(
        "baseball_mlb", bankroll=100, markets=["h2h", "player_points"], regions="us"
    )
    assert result["events"] == featured
    assert result["status"]["ok"] is True
    assert result["status"]["partial"] is True
    assert result["status"]["extended"]["error"] == "PROVIDER_HTTP_401"


def test_sport_scan_certifies_complete_only_when_all_requested_sections_succeed(monkeypatch):
    monkeypatch.setattr(provider, "fetch_featured_odds",
                        lambda *args, **kwargs: ([], {"ok": True, "partial": False}))
    monkeypatch.setattr(provider, "fetch_event_markets",
                        lambda *args, **kwargs: ([], {"ok": True, "partial": False}))
    result = provider.scan_sport_payload(
        "baseball_mlb", bankroll=100, markets=["h2h", "player_points"], regions="us"
    )
    assert result["status"]["ok"] is True
    assert result["status"]["partial"] is False


def test_duplicate_featured_event_id_preserves_quotes_but_cannot_certify_coverage(monkeypatch):
    first = _valid_game()
    second = _valid_game()
    second["bookmakers"][0]["key"] = "book-two"
    rows, meta = _fetch(monkeypatch, [first, second])
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert {q["book"] for q in rows[0]["quotes"]} == {"book-one", "book-two"}
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_games_duplicate_event_id"] == 1
    assert meta["error"] == "DUPLICATE_PROVIDER_FEATURED_EVENT_ID"


def test_distinct_featured_event_ids_remain_complete(monkeypatch):
    first = _valid_game()
    second = _valid_game()
    second["id"] = "event-2"
    rows, meta = _fetch(monkeypatch, [first, second])
    assert len(rows) == 2
    assert meta["ok"] is True
    assert meta["partial"] is False
    assert meta["n_games_duplicate_event_id"] == 0
