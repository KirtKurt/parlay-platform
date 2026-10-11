import importlib
import io
import json
import urllib.error
from datetime import datetime, timezone

from hello_world import mlb_odds_market_expansion_v8 as v8


def cfg(**overrides):
    base = dict(
        enabled=True,
        featured_regions=("us",),
        event_regions=("us", "us2"),
        featured_markets=("h2h", "spreads", "totals"),
        first_five_enabled=True,
        alternates_enabled=True,
        team_props_enabled=True,
        player_props_enabled=False,
        max_event_markets=18,
        max_events_per_cycle=8,
        max_estimated_credits_per_cycle=500,
    )
    base.update(overrides)
    return v8.V8Config(**base)


def test_featured_historical_url_contains_all_high_value_markets():
    url = v8.featured_odds_url(
        "secret",
        historical_at="2025-07-01T12:00:00Z",
        config=cfg(),
    )
    assert "/historical/sports/baseball_mlb/odds?" in url
    assert "regions=us" in url
    assert "markets=h2h%2Cspreads%2Ctotals" in url
    assert "date=2025-07-01T12%3A00%3A00Z" in url
    assert "includeSids=true" in url
    assert v8.estimate_featured_credits(cfg(), historical=True) == 30


def test_historical_events_url_uses_historical_endpoint():
    url = v8.events_url("secret", historical_at="2025-07-01T12:00:00Z")
    assert "/historical/sports/baseball_mlb/events?" in url
    assert "date=2025-07-01T12%3A00%3A00Z" in url


def test_event_market_selection_is_allowlisted_and_ordered():
    available = {
        "pitcher_strikeouts",
        "h2h_1st_5_innings",
        "alternate_totals",
        "totals_1st_5_innings",
        "unknown_market",
        "team_totals",
    }
    selected = v8.selected_event_markets(
        available,
        cfg(player_props_enabled=True),
    )
    assert selected == (
        "h2h_1st_5_innings",
        "totals_1st_5_innings",
        "alternate_totals",
        "team_totals",
        "pitcher_strikeouts",
    )
    assert "unknown_market" not in selected


def test_cycle_budget_includes_discovery_and_blocks_expensive_plan():
    value = v8.enforce_cycle_budget(
        event_count=8,
        event_market_count=40,
        config=cfg(max_estimated_credits_per_cycle=500),
    )
    assert value["featuredEstimatedCredits"] == 3
    assert value["discoveryEstimatedCredits"] == 8
    assert value["eventEstimatedCredits"] == 640
    assert value["estimatedCredits"] == 651
    assert value["withinBudget"] is False
    historical = v8.enforce_cycle_budget(
        event_count=1,
        event_market_count=3,
        config=cfg(max_estimated_credits_per_cycle=500),
        historical=True,
    )
    assert historical["discoveryEstimatedCredits"] == 0
    assert historical["estimatedCredits"] == 90


def test_normalization_and_side_specific_team_features():
    raw = {
        "id": "evt",
        "sport_key": "baseball_mlb",
        "commence_time": "2025-07-01T23:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [
            {
                "key": "book1",
                "title": "Book 1",
                "sid": "b1",
                "last_update": "2025-07-01T20:00:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home", "price": -130},
                            {"name": "Away", "price": 115},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 8.5},
                            {"name": "Under", "price": -110, "point": 8.5},
                        ],
                    },
                    {
                        "key": "totals_1st_5_innings",
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 4.5},
                            {"name": "Under", "price": -115, "point": 4.5},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Home", "price": -115, "point": -1.5},
                            {"name": "Away", "price": -105, "point": 1.5},
                        ],
                    },
                    {
                        "key": "spreads_1st_5_innings",
                        "outcomes": [
                            {"name": "Home", "price": -110, "point": -0.5},
                            {"name": "Away", "price": -110, "point": 0.5},
                        ],
                    },
                    {
                        "key": "pitcher_strikeouts",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Pitcher A",
                                "price": -120,
                                "point": 5.5,
                            }
                        ],
                    },
                ],
            }
        ],
    }
    event = v8.normalize_event(raw)
    features = v8.derive_team_level_features(event)
    assert event["fingerprint"]
    assert event["bookmakers"][0]["sid"] == "b1"
    assert features["totals_OverMedianPoint"] == 8.5
    assert features["totals_1st_5_innings_OverMedianPoint"] == 4.5
    assert features["impliedLateInningRunEnvironment"] == 4.0
    assert features["spreads_HomeMedianPoint"] == -1.5
    assert features["spreads_1st_5_innings_HomeMedianPoint"] == -0.5
    assert features["homeStarterBullpenSpreadDivergence"] == -1.0
    assert features["allowlistedPlayerPropObservationCount"] == 1
    assert features["playerPropsEligibleForTraining"] is False


def test_shadow_contract_never_changes_v7_authority():
    contract = v8.shadow_contract(cfg())
    assert contract["authority"] == "SHADOW_ONLY"
    assert contract["productionV7Unchanged"] is True
    assert contract["promotionRequiresUntouchedAudit80Pct"] is True
    assert contract["playerPropsRequireAuthoritativeTeamAttribution"] is True


def _collector_module(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("ODDS_API_KEY", "secret")
    monkeypatch.setenv("MLB_V8_SHADOW_BUCKET", "shadow-bucket")
    module = importlib.import_module("hello_world.mlb_odds_v8_shadow_collector")
    return importlib.reload(module)


def test_discovery_parser_reads_bookmaker_scoped_market_keys(monkeypatch):
    collector = _collector_module(monkeypatch)
    payload = {
        "bookmakers": [
            {
                "key": "book1",
                "markets": [
                    {"key": "h2h_1st_5_innings"},
                    {"key": "team_totals"},
                ],
            },
            {"key": "book2", "markets": [{"key": "alternate_totals"}]},
        ]
    }
    assert collector._available_market_keys(payload) == [
        "alternate_totals",
        "h2h_1st_5_innings",
        "team_totals",
    ]


def test_historical_plan_uses_allowlist_and_costs_ten_x(monkeypatch):
    collector = _collector_module(monkeypatch)
    plan = collector._historical_market_plan(cfg())
    assert plan[:3] == v8.FIRST_FIVE_MARKETS
    budget = v8.enforce_cycle_budget(
        event_count=1,
        event_market_count=len(plan),
        config=cfg(max_estimated_credits_per_cycle=5000),
        historical=True,
    )
    live_budget = v8.enforce_cycle_budget(
        event_count=1,
        event_market_count=len(plan),
        config=cfg(max_estimated_credits_per_cycle=5000),
        historical=False,
    )
    assert budget["eventEstimatedCredits"] == live_budget["eventEstimatedCredits"] * 10


def test_historical_batch_auto_sizes_to_credit_cap(monkeypatch):
    collector = _collector_module(monkeypatch)
    count, budget = collector._affordable_event_limit(8, cfg(), True)
    assert count == 1
    assert budget["withinBudget"] is True
    assert budget["estimatedCredits"] == 390


def test_historical_timestamp_validation(monkeypatch):
    collector = _collector_module(monkeypatch)
    assert (
        collector._validate_historical_at("2025-07-01T12:00:00Z")
        == "2025-07-01T12:00:00Z"
    )
    try:
        collector._validate_historical_at("2025-07-01 12:00")
    except ValueError as exc:
        assert "UTC ISO8601" in str(exc)
    else:
        raise AssertionError("invalid historical timestamp was accepted")


def _base_featured_event():
    return {
        "id": "evt",
        "sport_key": "baseball_mlb",
        "commence_time": "2025-07-01T23:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [
            {
                "key": "book1",
                "title": "Book 1",
                "markets": [
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -110, "point": 8.5},
                            {"name": "Under", "price": -110, "point": 8.5},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Home", "price": -115, "point": -1.5},
                            {"name": "Away", "price": -105, "point": 1.5},
                        ],
                    },
                ],
            }
        ],
    }


def _event_market_payload(market_key, outcomes):
    return {
        "id": "evt",
        "sport_key": "baseball_mlb",
        "commence_time": "2025-07-01T23:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "bookmakers": [
            {
                "key": "book1",
                "title": "Book 1",
                "markets": [{"key": market_key, "outcomes": outcomes}],
            }
        ],
    }


def test_isolated_market_responses_merge_before_cross_market_features(monkeypatch):
    collector = _collector_module(monkeypatch)
    responses = {
        "totals_1st_5_innings": _event_market_payload(
            "totals_1st_5_innings",
            [
                {"name": "Over", "price": -105, "point": 4.5},
                {"name": "Under", "price": -115, "point": 4.5},
            ],
        ),
        "spreads_1st_5_innings": _event_market_payload(
            "spreads_1st_5_innings",
            [
                {"name": "Home", "price": -110, "point": -0.5},
                {"name": "Away", "price": -110, "point": 0.5},
            ],
        ),
    }

    def fake_get(url):
        for key, payload in responses.items():
            if f"markets={key}" in url:
                return payload, {"x-requests-last": "2"}
        raise AssertionError(url)

    monkeypatch.setattr(collector, "_get", fake_get)
    row, errors = collector._fetch_event_markets_individually(
        _base_featured_event(),
        tuple(responses),
        None,
        cfg(),
    )
    assert errors == []
    assert row["successfulMarkets"] == list(responses)
    assert row["features"]["impliedLateInningRunEnvironment"] == 4.0
    assert row["features"]["homeStarterBullpenSpreadDivergence"] == -1.0
    markets = row["event"]["bookmakers"][0]["markets"]
    assert set(markets) == {
        "totals",
        "spreads",
        "totals_1st_5_innings",
        "spreads_1st_5_innings",
    }


def test_live_market_discovery_failure_is_fail_soft(monkeypatch):
    collector = _collector_module(monkeypatch)
    monkeypatch.setenv("MLB_V8_ENABLED", "true")
    monkeypatch.setenv("MLB_V8_MAX_EVENTS_PER_CYCLE", "2")
    monkeypatch.setenv("MLB_V8_MAX_CREDITS_PER_CYCLE", "100")
    captured = {}

    def fake_get(url):
        if "/events/evt/markets?" in url:
            raise urllib.error.HTTPError(url, 404, "unsupported", {}, None)
        if "/sports/baseball_mlb/odds?" in url:
            return [_base_featured_event()], {"x-requests-last": "3"}
        raise AssertionError(url)

    def fake_put(prefix, value):
        captured["prefix"] = prefix
        captured["value"] = value
        return {
            "bucket": "shadow-bucket",
            "key": f"{prefix}/digest.json",
            "sha256": "digest",
            "created": True,
        }

    monkeypatch.setattr(collector, "_get", fake_get)
    monkeypatch.setattr(collector, "_put_immutable", fake_put)
    value = collector.collect_once(trigger_mode="scheduled_shadow")
    assert value["ok"] is True
    assert value["status"] == "COLLECTED_SHADOW"
    assert value["triggerMode"] == "scheduled_shadow"
    assert value["selectedMarketRequestCount"] == 0
    assert value["eventEnrichmentCount"] == 1
    discovery = captured["value"]["discoveries"][0]
    assert discovery["discoveryMode"] == "LIVE_EVENT_MARKETS_FAIL_SOFT"
    assert discovery["status"] == 404
    assert captured["value"]["productionAuthorityChanged"] is False


def test_status_mode_reads_scheduled_artifact_without_provider_calls(monkeypatch):
    collector = _collector_module(monkeypatch)
    record = {
        "collectedAtUtc": "2026-07-26T07:15:00+00:00",
        "historicalAtUtc": None,
        "triggerMode": "scheduled_shadow",
        "contract": {"authority": "SHADOW_ONLY"},
        "budget": {"withinBudget": True, "estimatedCredits": 77},
        "productionAuthorityChanged": False,
    }
    body = json.dumps(record).encode("utf-8")

    class FakeS3:
        def list_objects_v2(self, **kwargs):
            assert kwargs["Bucket"] == "shadow-bucket"
            return {
                "IsTruncated": False,
                "Contents": [
                    {
                        "Key": "mlb/odds-v8-shadow/20260726/digest.json",
                        "LastModified": datetime(
                            2026, 7, 26, 7, 15, tzinfo=timezone.utc
                        ),
                        "Size": len(body),
                        "ETag": '"etag"',
                    }
                ],
            }

        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(body)}

    monkeypatch.setattr(collector, "_S3", FakeS3())
    monkeypatch.setattr(
        collector,
        "_get",
        lambda url: (_ for _ in ()).throw(AssertionError(url)),
    )
    value = collector.lambda_handler({"mode": "status", "limit": 5}, None)
    assert value["ok"] is True
    assert value["status"] == "SHADOW_STATUS"
    assert value["authority"] == "SHADOW_ONLY"
    assert value["productionAuthorityChanged"] is False
    artifact = value["latestArtifacts"][0]
    assert artifact["triggerMode"] == "scheduled_shadow"
    assert artifact["withinBudget"] is True
    assert artifact["estimatedCredits"] == 77
    assert artifact["parseError"] is None
