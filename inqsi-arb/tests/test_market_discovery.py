import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import market_discovery as discovery


def test_event_market_inventory_is_primary_and_not_static_catalog(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")

    def fake_get(url, params):
        assert url.endswith("/sports/baseball_mlb/events/event-1/markets")
        assert params["regions"] == "us,us2"
        return {
            "bookmakers": [
                {"key": "a", "markets": [{"key": "h2h"}, {"key": "batter_hits"}]},
                {"key": "b", "markets": [{"key": "batter_hits"}, {"key": "pitcher_outs"}]},
            ]
        }, {"ok": True, "status": 200}

    monkeypatch.setattr(discovery, "_get", fake_get)
    keys, meta = discovery.discover_event_market_keys(
        "baseball_mlb", "event-1", regions="us,us2", max_markets=250,
    )
    assert keys == ["h2h", "batter_hits", "pitcher_outs"]
    assert meta["discovery"] == "event_markets_endpoint"
    assert meta["fallback_used"] is False
    assert meta["partial"] is False


def test_event_market_inventory_respects_explicit_safety_cap(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery,
        "_get",
        lambda *_args, **_kwargs: (
            {"bookmakers": [{"key": "a", "markets": [{"key": f"m{i}"} for i in range(5)]}]},
            {"ok": True, "status": 200},
        ),
    )
    keys, meta = discovery.discover_event_market_keys(
        "basketball_nba", "event-2", max_markets=3,
    )
    assert keys == ["m0", "m1", "m2"]
    assert meta["partial"] is True


def test_catalog_probe_is_fail_closed_fallback_when_inventory_endpoint_fails(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery,
        "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": 503, "error": "UPSTREAM"}),
    )
    monkeypatch.setattr(
        discovery,
        "_probe_market_batch",
        lambda *_args, **_kwargs: (["h2h"], [], [{"ok": True, "status": 200}]),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-3")
    assert keys == ["h2h"]
    assert meta["fallback_used"] is True
    assert meta["discovery"] == "documented_catalog_runtime_probe_fallback"
    assert meta["event_markets_status"] == 503


def test_all_sports_all_markets_fans_out_instead_of_scanning_literal_all(monkeypatch):
    import app

    monkeypatch.setattr(
        app,
        "list_sports",
        lambda all_sports=False: (
            [{"key": "baseball_mlb"}, {"key": "basketball_nba"}],
            {"ok": True},
        ),
    )
    calls = []

    def fake_fetch(sport, **kwargs):
        calls.append((sport, kwargs))
        return [], {
            "ok": True,
            "stage": "complete",
            "n_events_discovered": 0,
            "n_events_requested": 0,
            "n_events_succeeded": 0,
            "n_normalized_markets": 0,
            "partial": False,
        }

    monkeypatch.setattr(app, "fetch_all_discovered_markets", fake_fetch)
    monkeypatch.setattr(app, "validate_events", lambda rows, jurisdiction="*": rows)
    monkeypatch.setattr(
        app,
        "scan_all",
        lambda payload: {
            "n_arbs": 0,
            "n_detected_unverified": 0,
            "n_rejected": 0,
            "n_held_unverified": 0,
            "n_exchange_pending": 0,
            "n_middles": 0,
            "hits": [],
            "detected_unverified": [],
            "rejected": [],
            "exchange_pending": [],
            "middles": [],
            "n_markets": 0,
        },
    )
    monkeypatch.setattr(app, "audit_enabled", lambda: False)

    response = app.lambda_handler(
        {
            "httpMethod": "GET",
            "path": "/v1/arb/scan",
            "queryStringParameters": {
                "sport": "all",
                "markets": "all",
                "source": "live",
                "max_events": "40",
            },
        },
        None,
    )
    import json

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert [sport for sport, _ in calls] == ["baseball_mlb", "basketball_nba"]
    assert body["status"]["n_sports_scanned"] == 2
    assert body["status"]["partial"] is False
    assert all(call[1]["max_markets_per_event"] == 250 for call in calls)


def test_probe_does_not_fan_out_on_auth_throttle_or_outage(monkeypatch):
    # These errors are not evidence that any individual market key is invalid.
    # In particular, a 401 must not trigger an exponential retry tree.
    for status, error in (
        (401, "PROVIDER_HTTP_401"),
        (403, "PROVIDER_HTTP_403"),
        (404, "PROVIDER_HTTP_404"),
        (408, "PROVIDER_HTTP_408"),
        (410, "PROVIDER_HTTP_410"),
        (429, "PROVIDER_HTTP_429"),
        (500, "PROVIDER_HTTP_500"),
        (503, "PROVIDER_HTTP_503"),
        (None, "PROVIDER_UNAVAILABLE"),
    ):
        calls = []

        def fake_request(sport, event, markets, *, regions, bookmakers=None):
            calls.append(tuple(markets))
            return None, {"ok": False, "status": status, "error": error}

        monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
        accepted, rows, evidence = discovery._probe_market_batch(
            "baseball_mlb", "event-1", ["h2h", "spreads", "totals", "player_points"],
            regions="us", bookmakers=None,
        )
        assert accepted == []
        assert rows == []
        assert len(calls) == 1
        assert evidence == [{
            "markets": ["h2h", "spreads", "totals", "player_points"],
            "ok": False, "status": status, "error": error, "depth": 0,
        }]


def test_invalid_market_still_bisects_and_retains_supported_neighbors(monkeypatch):
    calls = []

    def fake_request(sport, event, markets, *, regions, bookmakers=None):
        calls.append(tuple(markets))
        if list(markets) == ["h2h"]:
            return {
                "id": event,
                "home_team": "Home",
                "away_team": "Away",
                "bookmakers": [{
                    "key": "book_a",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Home", "price": -110},
                        {"name": "Away", "price": 110},
                    ]}],
                }],
            }, {"ok": True, "status": 200}
        return None, {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}

    monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
    accepted, rows, evidence = discovery._probe_market_batch(
        "baseball_mlb", "event-1", ["h2h", "invalid_market"],
        regions="us", bookmakers=None,
    )
    assert accepted == ["h2h"]
    assert rows and rows[0]["market"] == "h2h"
    assert calls == [("h2h", "invalid_market"), ("h2h",), ("invalid_market",)]
    assert len(evidence) == 3


def test_failed_fallback_preserves_provider_error_for_event_discovery(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery,
        "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (
            [], {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"}
        ),
    )
    monkeypatch.setattr(
        discovery,
        "_probe_market_batch",
        lambda *_args, **_kwargs: (
            [], [], [{"ok": False, "status": 401, "error": "PROVIDER_HTTP_401", "depth": 0}]
        ),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-401")
    assert keys == []
    assert meta["ok"] is False
    assert meta["error"] == "PROVIDER_HTTP_401"
    assert meta["status"] == 401
    assert meta["event_markets_error"] == "PROVIDER_HTTP_401"

    report = discovery.discover_sport_event_markets(
        "baseball_mlb", [{"id": "event-401"}]
    )
    assert report["ok"] is False
    assert report["errors"] == [{
        "event_id": "event-401",
        "error": "PROVIDER_HTTP_401",
        "status": 401,
    }]


def test_full_market_fetch_retains_per_event_failure_evidence(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "discover_events",
        lambda *_args, **_kwargs: (
            [{"id": "event-401"}], {"ok": True, "n_events": 1}
        ),
    )
    monkeypatch.setattr(
        discovery,
        "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (
            [], {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"}
        ),
    )
    monkeypatch.setattr(
        discovery,
        "_probe_market_batch",
        lambda *_args, **_kwargs: (
            [], [], [{"ok": False, "status": 401, "error": "PROVIDER_HTTP_401", "depth": 0}]
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets(
        "baseball_mlb", regions="us"
    )
    assert rows == []
    assert report["ok"] is False
    assert report["n_events_succeeded"] == 0
    detail = report["event_details"][0]
    assert detail["event_id"] == "event-401"
    assert detail["error"] == "PROVIDER_HTTP_401"
    assert detail["status"] == 401
    assert detail["event_markets_error"] == "PROVIDER_HTTP_401"
    assert detail["event_markets_status"] == 401
    assert detail["probe_requests"] == 1


def test_nested_auth_failure_not_masked_by_initial_unsupported_market(monkeypatch):
    """A 422 parent must not hide a terminal 401 from a child batch."""
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery, "candidate_markets_for_sport", lambda _sport: ["h2h", "spreads"]
    )
    monkeypatch.setattr(
        discovery,
        "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (
            [], {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}
        ),
    )
    calls = []

    def fake_request(_sport, _event, markets, *, regions, bookmakers=None):
        calls.append(tuple(markets))
        status = 401 if list(markets) == ["h2h"] else 422
        return None, {"ok": False, "status": status, "error": f"PROVIDER_HTTP_{status}"}

    monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-401")
    assert keys == []
    assert meta["ok"] is False
    assert meta["status"] == 401
    assert meta["error"] == "PROVIDER_HTTP_401"
    assert meta["event_markets_status"] == 422
    assert calls == [("h2h", "spreads"), ("h2h",), ("spreads",)]
