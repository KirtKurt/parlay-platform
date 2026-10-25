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
    # A catalog fallback is useful but cannot establish full provider coverage.
    assert meta["partial"] is True


def test_full_market_fetch_marks_static_fallback_coverage_partial(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: ([{"id": "event-1"}], {"ok": True, "n_events": 1}),
    )
    monkeypatch.setattr(discovery, "candidate_markets_for_sport", lambda _sport: ["h2h"])
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": 503, "error": "UPSTREAM"}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}], [{"ok": True, "status": 200, "error": None}]
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == [{"market": "h2h"}]
    assert report["ok"] is True
    assert report["n_events_succeeded"] == 1
    assert report["partial"] is True
    assert report["event_details"][0]["fallback_used"] is True
    assert report["event_details"][0]["partial"] is True


def test_full_market_fetch_primary_inventory_can_be_complete(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: ([{"id": "event-1"}], {"ok": True, "n_events": 1}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h"], {"ok": True, "status": 200}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}], [{"ok": True, "status": 200, "error": None}]
        ),
    )
    _, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert report["ok"] is True
    assert report["partial"] is False
    assert report["event_details"][0]["fallback_used"] is False
    assert report["event_details"][0]["partial"] is False


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


def test_full_market_fetch_marks_missing_observed_market_odds_partial(monkeypatch):
    # Successful inventory does not prove complete odds coverage if one of its
    # observed market keys fails to return a price in the live odds probe.
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: ([{"id": "event-1"}], {"ok": True, "n_events": 1}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h", "totals"], {"ok": True, "status": 200}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}], [{"ok": True, "status": 200}]
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == [{"market": "h2h"}]
    assert report["ok"] is True
    assert report["event_details"][0]["n_observed"] == 2
    assert report["event_details"][0]["n_discovered"] == 1
    assert report["event_details"][0]["fallback_used"] is False
    assert report["event_details"][0]["partial"] is True
    assert report["partial"] is True


def test_full_market_fetch_marks_failed_event_partial_despite_other_success(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (
            [{"id": "event-good"}, {"id": "event-failed"}],
            {"ok": True, "n_events": 2},
        ),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h"], {"ok": True, "status": 200}),
    )

    def fake_probe(_sport, event_id, _markets, **_kwargs):
        if event_id == "event-good":
            return ["h2h"], [{"market": "h2h"}], [{"ok": True, "status": 200}]
        return [], [], [{"ok": False, "status": 503, "error": "PROVIDER_HTTP_503"}]

    monkeypatch.setattr(discovery, "_probe_market_batch", fake_probe)
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == [{"market": "h2h"}]
    assert report["ok"] is True
    assert report["n_events_requested"] == 2
    assert report["n_events_succeeded"] == 1
    assert report["event_details"][1]["ok"] is False
    assert report["event_details"][1]["partial"] is True
    assert report["partial"] is True


def test_aggregate_event_market_report_marks_fallback_partial(monkeypatch):
    # A successful static-catalog fallback still cannot prove full live coverage.
    monkeypatch.setattr(
        discovery, "discover_event_market_keys",
        lambda *_args, **_kwargs: (
            ["h2h"], {"ok": True, "partial": True, "fallback_used": True}
        ),
    )
    report = discovery.discover_sport_event_markets(
        "baseball_mlb", [{"id": "event-1"}]
    )
    assert report["ok"] is True
    assert report["events"] == {"event-1": ["h2h"]}
    assert report["partial"] is True
    assert report["fallback_events"] == ["event-1"]
    assert report["n_events_available"] == 1
    assert report["n_events_requested"] == 1


def test_aggregate_event_market_report_marks_failed_event_partial(monkeypatch):
    def fake_discover(_sport, event_id, **_kwargs):
        if event_id == "event-good":
            return ["h2h"], {"ok": True, "partial": False, "fallback_used": False}
        return [], {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"}

    monkeypatch.setattr(discovery, "discover_event_market_keys", fake_discover)
    report = discovery.discover_sport_event_markets(
        "baseball_mlb", [{"id": "event-good"}, {"id": "event-failed"}]
    )
    assert report["ok"] is True
    assert report["events"] == {"event-good": ["h2h"]}
    assert report["partial"] is True
    assert report["errors"] == [{
        "event_id": "event-failed",
        "status": 401,
        "error": "PROVIDER_HTTP_401",
    }]


def test_aggregate_event_market_report_respects_event_and_market_caps(monkeypatch):
    def fake_discover(_sport, event_id, **_kwargs):
        return ["h2h"], {
            "ok": True,
            "partial": event_id == "event-truncated-markets",
            "fallback_used": False,
        }

    monkeypatch.setattr(discovery, "discover_event_market_keys", fake_discover)
    events = [{"id": "event-good"}, {"id": "event-truncated-markets"}]
    complete = discovery.discover_sport_event_markets(
        "baseball_mlb", events[:1]
    )
    assert complete["partial"] is False
    assert complete["fallback_events"] == []
    capped = discovery.discover_sport_event_markets(
        "baseball_mlb", events, max_events=1
    )
    assert capped["partial"] is True
    assert capped["n_events_available"] == 2
    assert capped["n_events_requested"] == 1
    market_capped = discovery.discover_sport_event_markets(
        "baseball_mlb", events
    )
    assert market_capped["partial"] is True
    assert market_capped["n_events"] == 2



def test_partial_fallback_preserves_terminal_provider_error_with_accepted_market(monkeypatch):
    # A successful child market must not hide a 401 in a sibling probe.
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(discovery, "candidate_markets_for_sport", lambda _sport: ["h2h", "totals"])
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}],
            [
                {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"},
                {"ok": True, "status": 200, "error": None},
                {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"},
            ],
        ),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-partial")
    assert keys == ["h2h"]
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["status"] == 401
    assert meta["error"] == "PROVIDER_HTTP_401"
    assert meta["event_markets_status"] == 422


def test_full_market_partial_success_preserves_terminal_provider_error(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: ([{"id": "event-partial"}], {"ok": True, "n_events": 1}),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h", "totals"], {"ok": True, "status": 200}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}],
            [
                {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"},
                {"ok": True, "status": 200, "error": None},
                {"ok": False, "status": 429, "error": "PROVIDER_HTTP_429"},
            ],
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets("baseball_mlb", regions="us")
    assert rows == [{"market": "h2h"}]
    assert report["ok"] is True
    assert report["partial"] is True
    detail = report["event_details"][0]
    assert detail["ok"] is True
    assert detail["partial"] is True
    assert detail["status"] == 429
    assert detail["error"] == "PROVIDER_HTTP_429"


def test_partial_success_with_only_unsupported_market_does_not_invent_terminal_error(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(discovery, "candidate_markets_for_sport", lambda _sport: ["h2h", "bad"])
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: ([], {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}],
            [
                {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"},
                {"ok": True, "status": 200, "error": None},
                {"ok": False, "status": 422, "error": "PROVIDER_HTTP_422"},
            ],
        ),
    )
    keys, meta = discovery.discover_event_market_keys("baseball_mlb", "event-unsupported")
    assert keys == ["h2h"]
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["status"] is None
    assert meta["error"] is None


def test_probe_unknown_statuses_do_not_recursively_fan_out(monkeypatch):
    # A failure of unknown cause is not evidence of an invalid market key.
    # Do not amplify upstream requests or conceal the original failure.
    for status in (None, 200, 302, 402, 409, 418, 451):
        calls = []

        def fake_request(_sport, _event, markets, *, regions, bookmakers=None):
            calls.append(tuple(markets))
            return None, {
                "ok": status == 200,
                "status": status,
                "error": "MALFORMED_RESPONSE" if status == 200 else "UNEXPECTED_PROVIDER_STATUS",
            }

        monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
        accepted, rows, evidence = discovery._probe_market_batch(
            "baseball_mlb", "event-1", ["h2h", "totals", "spreads"],
            regions="us", bookmakers=None,
        )
        assert accepted == []
        assert rows == []
        assert calls == [("h2h", "totals", "spreads")]
        assert len(evidence) == 1
        assert evidence[0]["status"] == status


def test_http_400_invalid_market_still_isolates_supported_neighbor(monkeypatch):
    calls = []

    def fake_request(_sport, _event, markets, *, regions, bookmakers=None):
        calls.append(tuple(markets))
        if list(markets) == ["h2h"]:
            return {"bookmakers": [{"key": "book_a", "markets": [{"key": "h2h"}]}]}, {
                "ok": True, "status": 200,
            }
        return None, {"ok": False, "status": 400, "error": "INVALID_MARKET"}

    monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
    monkeypatch.setattr(discovery, "normalize_games", lambda *_args, **_kwargs: [{"market": "h2h"}])
    accepted, rows, evidence = discovery._probe_market_batch(
        "baseball_mlb", "event-1", ["h2h", "bad_market"],
        regions="us", bookmakers=None,
    )
    assert accepted == ["h2h"]
    assert rows == [{"market": "h2h"}]
    assert calls == [("h2h", "bad_market"), ("h2h",), ("bad_market",)]
    assert len(evidence) == 3


def test_malformed_live_inventory_fails_closed_to_partial_fallback(monkeypatch):
    # A 200 with a broken inventory shape must not certify zero missing markets.
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(discovery, "candidate_markets_for_sport", lambda _sport: ["h2h"])
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}],
            [{"ok": True, "status": 200, "error": None}],
        ),
    )
    malformed = (
        None,
        {},
        {"bookmakers": None},
        {"bookmakers": {"key": "book_a"}},
        {"bookmakers": [None]},
        {"bookmakers": [{"key": "book_a", "markets": None}]},
        {"bookmakers": [{"key": "book_a", "markets": [None]}]},
    )
    for payload in malformed:
        monkeypatch.setattr(
            discovery, "_get",
            lambda *_args, _payload=payload, **_kwargs: (
                _payload, {"ok": True, "status": 200}
            ),
        )
        keys, meta = discovery.discover_event_market_keys(
            "baseball_mlb", "event-malformed"
        )
        assert keys == ["h2h"]
        assert meta["fallback_used"] is True
        assert meta["partial"] is True
        assert meta["event_markets_error"] == "MALFORMED_PROVIDER_MARKET_INVENTORY"


def test_valid_empty_live_inventory_does_not_invent_market_keys(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery, "_get",
        lambda *_args, **_kwargs: (
            {"bookmakers": []}, {"ok": True, "status": 200}
        ),
    )
    keys, meta = discovery.discover_event_market_keys(
        "baseball_mlb", "event-no-books"
    )
    assert keys == []
    assert meta["ok"] is True
    assert meta["fallback_used"] is False
    assert meta["partial"] is False


def test_malformed_successful_event_odds_fail_closed_without_fanout(monkeypatch):
    # HTTP 200 alone must not crash normalization or certify malformed quotes.
    malformed = (
        None,
        [],
        {"bookmakers": None},
        {"bookmakers": [None]},
        {"bookmakers": [{"markets": {"key": "h2h"}}]},
        {"bookmakers": [{"markets": [None]}]},
        {"bookmakers": [{"markets": [{"key": "h2h", "outcomes": [None]}]}]},
        {"bookmakers": [{"markets": [{"key": "h2h", "outcomes": {}}]}]},
    )
    for payload in malformed:
        calls = []

        def fake_request(_sport, _event, markets, *, regions, bookmakers=None):
            calls.append(tuple(markets))
            return payload, {"ok": True, "status": 200}

        monkeypatch.setattr(discovery, "_request_event_odds", fake_request)
        accepted, rows, evidence = discovery._probe_market_batch(
            "baseball_mlb", "event-malformed", ["h2h", "totals"],
            regions="us", bookmakers=None,
        )
        assert accepted == []
        assert rows == []
        assert calls == [("h2h", "totals")]
        assert len(evidence) == 1
        assert evidence[0]["ok"] is False
        assert evidence[0]["error"] == "MALFORMED_PROVIDER_EVENT_ODDS"
        assert evidence[0]["status"] == 200


def test_full_market_fetch_reports_malformed_odds_as_partial(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (
            [{"id": "event-malformed"}], {"ok": True, "n_events": 1}
        ),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h"], {"ok": True, "status": 200}),
    )
    monkeypatch.setattr(
        discovery, "_request_event_odds",
        lambda *_args, **_kwargs: (
            {"bookmakers": [{"markets": [None]}]},
            {"ok": True, "status": 200},
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets(
        "baseball_mlb", regions="us"
    )
    assert rows == []
    assert report["ok"] is False
    assert report["partial"] is True
    assert report["n_events_succeeded"] == 0
    detail = report["event_details"][0]
    assert detail["ok"] is False
    assert detail["partial"] is True
    assert detail["error"] == "MALFORMED_PROVIDER_EVENT_ODDS"
    assert detail["status"] == 200
    assert detail["probe_requests"] == 1


def test_discover_events_preserves_partial_coverage_for_malformed_provider_rows(monkeypatch):
    monkeypatch.setattr(discovery, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        discovery, "_get",
        lambda *_args, **_kwargs: (
            [{"id": "valid"}, None, {"name": "missing-id"}, "invalid"],
            {"ok": True, "status": 200},
        ),
    )
    events, meta = discovery.discover_events("baseball_mlb")
    assert events == [{"id": "valid"}]
    assert meta["n_events"] == 1
    assert meta["n_events_received"] == 4
    assert meta["n_events_invalid"] == 3
    assert meta["partial"] is True


def test_full_market_report_inherits_partial_event_catalog(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_events",
        lambda *_args, **_kwargs: (
            [{"id": "valid"}],
            {"ok": True, "n_events": 1, "n_events_invalid": 2, "partial": True},
        ),
    )
    monkeypatch.setattr(
        discovery, "_discover_event_markets_direct",
        lambda *_args, **_kwargs: (["h2h"], {"ok": True, "status": 200}),
    )
    monkeypatch.setattr(
        discovery, "_probe_market_batch",
        lambda *_args, **_kwargs: (
            ["h2h"], [{"market": "h2h"}], [{"ok": True, "status": 200}],
        ),
    )
    rows, report = discovery.fetch_all_discovered_markets(
        "baseball_mlb", regions="us"
    )
    assert rows == [{"market": "h2h"}]
    assert report["ok"] is True
    assert report["n_events_invalid"] == 2
    assert report["event_details"][0]["partial"] is False
    assert report["partial"] is True


def test_aggregate_event_market_report_handles_invalid_event_objects(monkeypatch):
    monkeypatch.setattr(
        discovery, "discover_event_market_keys",
        lambda *_args, **_kwargs: (
            ["h2h"], {"ok": True, "partial": False, "fallback_used": False},
        ),
    )
    report = discovery.discover_sport_event_markets(
        "baseball_mlb", [None, {"id": "valid"}, "invalid", {"name": "no-id"}]
    )
    assert report["ok"] is True
    assert report["events"] == {"valid": ["h2h"]}
    assert report["n_events_available"] == 4
    assert report["partial"] is True
