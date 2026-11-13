"""Retain provider-integrity evidence when a neighboring market still has prices."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


@pytest.mark.parametrize("error", [
    "MALFORMED_PROVIDER_EVENT_ODDS",
    "MISMATCHED_PROVIDER_EVENT_ID",
    "MISMATCHED_PROVIDER_SPORT_KEY",
    "NO_USABLE_PROVIDER_MARKET_ODDS",
    "INVALID_PROVIDER_OUTCOME_DESCRIPTION",
])
def test_terminal_integrity_failure_survives_successful_sibling(error):
    evidence = [
        {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"},
        {"ok": True, "status": 200, "error": None},
        {"ok": False, "status": 200, "error": error},
    ]
    assert discovery._primary_probe_failure(evidence, terminal_only=True) == evidence[-1]


def test_full_scan_retains_valid_quote_and_surfaces_malformed_sibling(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda _sport: ([{"id": "event-1"}], {"ok": True, "partial": False}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h", "totals"], {"ok": True, "status": 200}),
    )
    calls = []

    def fake_request(sport, event, markets, *, regions, bookmakers=None):
        calls.append(tuple(markets))
        if len(markets) > 1:
            return None, {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}
        if markets[0] == "h2h":
            return {
                "id": event,
                "home_team": "Home",
                "away_team": "Away",
                "bookmakers": [{
                    "key": "book_a",
                    "markets": [{
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home", "price": -110},
                            {"name": "Away", "price": 110},
                        ],
                    }],
                }],
            }, {"ok": True, "status": 200}
        return {"id": event, "bookmakers": "invalid"}, {"ok": True, "status": 200}

    monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
    rows, report = discovery.fetch_all_discovered_markets(
        "baseball_mlb", regions="us", max_events=1,
    )
    assert calls == [("h2h", "totals"), ("h2h",), ("totals",)]
    assert [row["market"] for row in rows] == ["h2h"]
    assert report["ok"] is True
    assert report["partial"] is True
    detail = report["event_details"][0]
    assert detail["partial"] is True
    assert detail["error"] == "MALFORMED_PROVIDER_EVENT_ODDS"
    assert detail["n_discovered"] == 1


def test_full_scan_retains_description_failure_with_valid_sibling_quotes(monkeypatch):
    """A 422 split must not hide malformed player identity behind valid prices."""
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda _sport: ([{"id": "event-1"}], {"ok": True, "partial": False}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_a, **_kw: (["h2h", "player_points"], {"ok": True, "status": 200}),
    )

    def fake_request(sport, event, markets, *, regions, bookmakers=None):
        if len(markets) > 1:
            return None, {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}
        market = markets[0]
        outcomes = (
            [{"name": "Home", "price": -110}, {"name": "Away", "price": 110}]
            if market == "h2h" else [
                {"name": "Over", "description": "Player One", "price": -110},
                {"name": "Under", "description": "Player One", "price": 110},
                {"name": "Over", "description": 17, "price": 120},
            ]
        )
        return {
            "id": event, "sport_key": sport,
            "home_team": "Home", "away_team": "Away",
            "bookmakers": [{"key": "book-a", "markets": [
                {"key": market, "outcomes": outcomes},
            ]}],
        }, {"ok": True, "status": 200}

    monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
    rows, report = discovery.fetch_all_discovered_markets(
        "baseball_mlb", regions="us", max_events=1,
    )
    assert {row["market"] for row in rows} == {"h2h", "player_points"}
    assert report["ok"] is True
    assert report["partial"] is True
    detail = report["event_details"][0]
    assert detail["partial"] is True
    assert detail["error"] == "INVALID_PROVIDER_OUTCOME_DESCRIPTION"
    assert detail["n_discovered"] == 2
