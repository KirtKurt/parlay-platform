from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import mlb_v8_historical_point_in_time_context_v1 as context


def test_weather_run_is_conservatively_available_before_lock():
    run = context.latest_conservatively_available_weather_run(
        "2026-07-01T22:15:00Z"
    )

    assert run.isoformat() == "2026-07-01T12:00:00+00:00"
    assert (
        run.timestamp() + context.WEATHER_PUBLICATION_LAG_HOURS * 3600
        <= context._parse_utc("2026-07-01T22:15:00Z").timestamp()
    )


def test_injury_state_uses_only_transactions_through_cutoff():
    rows = [
        {
            "id": 1,
            "effectiveDate": "2026-04-01",
            "description": "Pitcher A placed on the 15-day injured list",
            "person": {"id": 10, "fullName": "Pitcher A"},
        },
        {
            "id": 2,
            "effectiveDate": "2026-04-20",
            "description": "Pitcher A reinstated from the 15-day injured list",
            "person": {"id": 10, "fullName": "Pitcher A"},
        },
        {
            "id": 3,
            "effectiveDate": "2026-04-25",
            "description": "Hitter B placed on the 10-day injured list",
            "person": {"id": 11, "fullName": "Hitter B"},
        },
        {
            "id": 4,
            "effectiveDate": "2026-05-01",
            "description": "Hitter C placed on the 10-day injured list",
            "person": {"id": 12, "fullName": "Hitter C"},
        },
    ]

    active = context.reconstruct_active_injuries(rows, date(2026, 4, 30))

    assert [row["name"] for row in active] == ["Hitter B"]
    assert active[0]["source"] == "official_mlb_transaction"


def test_weather_factor_is_bounded_and_uses_forecast_values_only():
    factor = context.weather_run_factor(
        {
            "temperature_2m": 35,
            "relative_humidity_2m": 80,
            "wind_speed_10m": 40,
            "precipitation_probability": 0,
        }
    )

    assert 0.85 <= factor <= 1.15
    assert factor > 1.0


def test_crosswalk_registry_drives_isolated_resource_bundle():
    context._CANONICAL_BY_PROVIDER_ID.clear()

    class Module:
        @staticmethod
        def crosswalk_provider_rows(_provider_rows, canonical_games, **_kwargs):
            return {
                "accepted": {
                    "123": {
                        "providerMatchId": "bbs-1",
                    }
                }
            }

    context.install_crosswalk_registry(Module)
    Module.crosswalk_provider_rows(
        [{"match_id": "bbs-1"}],
        [
            {
                "officialGamePk": "123",
                "slateDateEt": "2026-07-01",
                "commenceTime": "2026-07-01T23:00:00Z",
                "predictionLockAtUtc": "2026-07-01T22:15:00Z",
            }
        ],
    )

    class FakeSource:
        def build_bundle(self, canonical, stored_pitchers, stored_lineups):
            assert canonical["officialGamePk"] == "123"
            assert stored_pitchers["data"]["home"]["confirmed"] is True
            assert stored_lineups["data"]["away"]["confirmed"] is True
            return {
                name: {
                    "data": {"name": name},
                    "meta": {"asOfUtc": "2026-07-01T22:00:00Z"},
                    "error": None,
                }
                for name in (
                    "pitchers",
                    "lineups",
                    "bullpens",
                    "team_context",
                    "injuries",
                    "park",
                    "weather",
                )
            }

    class Client:
        def get_mlb_match_resource(
            self, _match_id, resource, *, game_date=None, as_of=None
        ):
            if resource == "pitchers":
                data = {"home": {"confirmed": True}, "away": {"confirmed": True}}
            else:
                data = {"home": {"confirmed": True}, "away": {"confirmed": True}}
            return {
                "data": data,
                "meta": {"asOfUtc": as_of},
                "error": None,
            }

    context.install_resource_provider(Client, source_factory=FakeSource)
    client = Client()
    weather = client.get_mlb_match_resource(
        "bbs-1",
        "weather",
        game_date="2026-07-01",
        as_of="2026-07-01T22:15:00Z",
    )

    assert weather["data"]["name"] == "weather"


def test_optional_park_and_weather_are_hard_point_in_time_requirements():
    def parse(value):
        return context._parse_utc(value)

    def effective(envelope):
        return parse((envelope.get("meta") or {}).get("asOfUtc"))

    module = SimpleNamespace(
        point_in_time_errors=lambda _resources, _lock: [],
        OPTIONAL_RESOURCES=("weather", "park"),
        _parse_time=parse,
        _effective_at=effective,
    )
    context.install_strict_optional_point_in_time_gate(module)

    errors = module.point_in_time_errors(
        {
            "weather": {
                "data": {},
                "meta": {"asOfUtc": "2026-07-01T22:20:00Z"},
                "error": None,
            },
            "park": {
                "data": {},
                "meta": {"asOfUtc": "2026-07-01T22:00:00Z"},
                "error": None,
            },
        },
        "2026-07-01T22:15:00Z",
    )

    assert errors == ["weather_source_effective_time_after_lock"]


def test_resource_shapes_satisfy_existing_fundamentals_contract():
    from datetime import datetime, timezone

    import mlb_v8_fundamentals_collector as collector

    timestamp = "2026-07-01T22:00:00Z"
    players = [
        {"slot": index, "id": index, "name": f"Player {index}", "ops": 0.75}
        for index in range(1, 10)
    ]
    resources = {
        "pitchers": {
            "data": {
                side: {
                    "id": f"{side}-starter",
                    "name": f"{side} starter",
                    "confirmed": True,
                    "stats": {"era": 3.5, "fip": 3.6, "kMinusBbPct": 18.0},
                    "expectedInnings": 5.8,
                    "recentThreeStarts": {"fip": 3.2},
                }
                for side in ("away", "home")
            },
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "lineups": {
            "data": {
                side: {"confirmed": True, "players": players}
                for side in ("away", "home")
            },
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "bullpens": {
            "data": {
                side: {
                    "era": 3.8,
                    "fip": 3.9,
                    "last3DaysInnings": 4.0,
                    "last2DaysPitches": 80,
                    "closerAvailable": True,
                    "highLeverageAvailable": True,
                    "expectedInnings": 3.2,
                    "availableRelievers": 7,
                }
                for side in ("away", "home")
            },
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "injuries": {
            "data": {"away": [], "home": []},
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "team_context": {
            "data": {
                side: {
                    "id": side,
                    "name": side,
                    "record": {"wins": 45, "losses": 35},
                    "recentForm": {"winRate": 0.6},
                    "homeAwaySplit": {"winRate": 0.55},
                    "restDays": 1,
                    "travel": {"miles": 250},
                    "defense": {"rating": -4.1},
                }
                for side in ("away", "home")
            },
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "park": {
            "data": {"runFactor": 1.02},
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
        "weather": {
            "data": {"runFactor": 1.01},
            "meta": {
                "confirmed": True,
                "asOfUtc": timestamp,
                "updatedAt": timestamp,
            },
            "error": None,
        },
    }

    game = collector.normalize_match(
        {
            "id": "bbs-1",
            "date": "2026-07-01",
            "startTime": "2026-07-01T23:00:00Z",
            "away": {"id": "away", "name": "Away"},
            "home": {"id": "home", "name": "Home"},
        },
        datetime.now(timezone.utc),
        resources,
    )

    assert game["coverage"]["trainingEligible"] is True
    assert game["coverage"]["confirmedStarters"] is True
    assert game["coverage"]["confirmedLineups"] is True


def test_strict_prior_cutoff_uses_previous_et_slate_day_not_lock_utc_day():
    cutoff = context._strict_prior_cutoff(
        {
            "slateDateEt": "2026-07-01",
            "predictionLockAtUtc": "2026-07-02T01:15:00Z",
        }
    )

    assert cutoff.isoformat() == "2026-07-01T03:59:59+00:00"
    assert cutoff < context._parse_utc("2026-07-02T01:15:00Z")


def test_official_identity_fallback_covers_canonical_game_without_bbs_row():
    context.SYNTHETIC_OFFICIAL_IDENTITY_COUNT = 0

    class Module:
        @staticmethod
        def crosswalk_provider_rows(_provider_rows, _canonical_games, **_kwargs):
            return {
                "accepted": {},
                "acceptedCount": 0,
                "quarantined": [],
                "quarantinedCount": 0,
                "selectionUsedOutcomes": False,
            }

    context.install_official_identity_fallback(Module)
    value = Module.crosswalk_provider_rows(
        [],
        [
            {
                "officialGamePk": "123",
                "slateDateEt": "2025-06-01",
                "commenceTime": "2025-06-01T17:05:00Z",
                "predictionLockAtUtc": "2025-06-01T16:20:00Z",
                "homeTeam": "Home",
                "awayTeam": "Away",
            }
        ],
    )

    row = value["accepted"]["123"]
    assert row["providerMatchId"] == "official-mlb:123"
    assert (
        row["crosswalkMethod"]
        == "DIRECT_CANONICAL_OFFICIAL_GAME_ID_CONTEXT_FALLBACK"
    )
    assert row["syntheticOfficialIdentity"] is True
    assert value["completeCanonicalCoverage"] is True
    assert value["selectionUsedOutcomes"] is False
