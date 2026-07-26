import importlib

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
    url = v8.featured_odds_url("secret", historical_at="2025-07-01T12:00:00Z", config=cfg())
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
        "pitcher_strikeouts", "h2h_1st_5_innings", "alternate_totals",
        "totals_1st_5_innings", "unknown_market", "team_totals",
    }
    selected = v8.selected_event_markets(available, cfg(player_props_enabled=True))
    assert selected == (
        "h2h_1st_5_innings", "totals_1st_5_innings", "alternate_totals",
        "team_totals", "pitcher_strikeouts",
    )
    assert "unknown_market" not in selected


def test_cycle_budget_includes_discovery_and_blocks_expensive_plan():
    value = v8.enforce_cycle_budget(
        event_count=8, event_market_count=40,
        config=cfg(max_estimated_credits_per_cycle=500),
    )
    assert value["featuredEstimatedCredits"] == 3
    assert value["discoveryEstimatedCredits"] == 8
    assert value["eventEstimatedCredits"] == 640
    assert value["estimatedCredits"] == 651
    assert value["withinBudget"] is False
    historical = v8.enforce_cycle_budget(
        event_count=1, event_market_count=3,
        config=cfg(max_estimated_credits_per_cycle=500), historical=True,
    )
    assert historical["discoveryEstimatedCredits"] == 0
    assert historical["estimatedCredits"] == 90


def test_normalization_and_side_specific_team_features():
    raw = {
        "id": "evt", "sport_key": "baseball_mlb",
        "commence_time": "2025-07-01T23:00:00Z",
        "home_team": "Home", "away_team": "Away",
        "bookmakers": [{
            "key": "book1", "title": "Book 1", "sid": "b1",
            "last_update": "2025-07-01T20:00:00Z",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Home", "price": -130}, {"name": "Away", "price": 115}
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": -110, "point": 8.5},
                    {"name": "Under", "price": -110, "point": 8.5},
                ]},
                {"key": "totals_1st_5_innings", "outcomes": [
                    {"name": "Over", "price": -105, "point": 4.5},
                    {"name": "Under", "price": -115, "point": 4.5},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "Home", "price": -115, "point": -1.5},
                    {"name": "Away", "price": -105, "point": 1.5},
                ]},
                {"key": "spreads_1st_5_innings", "outcomes": [
                    {"name": "Home", "price": -110, "point": -0.5},
                    {"name": "Away", "price": -110, "point": 0.5},
                ]},
                {"key": "pitcher_strikeouts", "outcomes": [
                    {"name": "Over", "description": "Pitcher A", "price": -120, "point": 5.5}
                ]},
            ],
        }],
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
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    module = importlib.import_module("hello_world.mlb_odds_v8_shadow_collector")
    return importlib.reload(module)


def test_discovery_parser_reads_bookmaker_scoped_market_keys(monkeypatch):
    collector = _collector_module(monkeypatch)
    payload = {
        "bookmakers": [
            {"key": "book1", "markets": [{"key": "h2h_1st_5_innings"}, {"key": "team_totals"}]},
            {"key": "book2", "markets": [{"key": "alternate_totals"}]},
        ]
    }
    assert collector._available_market_keys(payload) == [
        "alternate_totals", "h2h_1st_5_innings", "team_totals"
    ]


def test_historical_plan_uses_allowlist_and_costs_ten_x(monkeypatch):
    collector = _collector_module(monkeypatch)
    plan = collector._historical_market_plan(cfg())
    assert plan[:3] == v8.FIRST_FIVE_MARKETS
    budget = v8.enforce_cycle_budget(
        event_count=1, event_market_count=len(plan),
        config=cfg(max_estimated_credits_per_cycle=5000), historical=True,
    )
    live_budget = v8.enforce_cycle_budget(
        event_count=1, event_market_count=len(plan),
        config=cfg(max_estimated_credits_per_cycle=5000), historical=False,
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
    assert collector._validate_historical_at("2025-07-01T12:00:00Z") == "2025-07-01T12:00:00Z"
    try:
        collector._validate_historical_at("2025-07-01 12:00")
    except ValueError as exc:
        assert "UTC ISO8601" in str(exc)
    else:
        raise AssertionError("invalid historical timestamp was accepted")
