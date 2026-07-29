from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

import run_mlb_v8_historical_bbs_backfill as backfill
import run_mlb_v8_historical_bbs_backfill_entrypoint as entrypoint


def missing_stack_error():
    return ClientError(
        {
            "Error": {
                "Code": "ValidationError",
                "Message": "Stack with id optional-fundamentals does not exist",
            }
        },
        "DescribeStacks",
    )


def test_missing_optional_stack_uses_historical_artifacts_bucket():
    calls = []

    def outputs(_cf, stack_name):
        calls.append(stack_name)
        if stack_name == "optional-fundamentals":
            raise missing_stack_error()
        return {"HistoricalArtifactsBucketName": "historical-live-bucket"}

    module = SimpleNamespace(_outputs=outputs)
    entrypoint.install_bucket_fallback(
        module,
        historical_stack="historical-live",
        fundamentals_stack="optional-fundamentals",
    )

    value = module._outputs(object(), "optional-fundamentals")

    assert value["FundamentalsArtifactsBucketName"] == "historical-live-bucket"
    assert value["HistoricalBbsManifestBucketSource"] == entrypoint.VERSION
    assert calls == ["optional-fundamentals", "historical-live"]


def test_existing_optional_stack_remains_authoritative():
    module = SimpleNamespace(
        _outputs=lambda _cf, _stack: {
            "FundamentalsArtifactsBucketName": "isolated-fundamentals-bucket"
        }
    )
    entrypoint.install_bucket_fallback(
        module,
        historical_stack="historical-live",
        fundamentals_stack="optional-fundamentals",
    )

    value = module._outputs(object(), "optional-fundamentals")

    assert value == {
        "FundamentalsArtifactsBucketName": "isolated-fundamentals-bucket"
    }


def test_non_missing_cloudformation_error_is_not_hidden():
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "DescribeStacks",
    )

    def outputs(_cf, _stack):
        raise denied

    module = SimpleNamespace(_outputs=outputs)
    entrypoint.install_bucket_fallback(
        module,
        historical_stack="historical-live",
        fundamentals_stack="optional-fundamentals",
    )

    with pytest.raises(ClientError):
        module._outputs(object(), "optional-fundamentals")


def test_historical_discovery_forces_stored_match_surface():
    class Client:
        calls = []

        def list_mlb_matches(self, game_date, *, limit=50, as_of=None, stored=False):
            self.calls.append(
                {
                    "gameDate": game_date,
                    "limit": limit,
                    "asOf": as_of,
                    "stored": stored,
                }
            )
            return {"data": []}

    entrypoint.install_stored_match_surface(Client)
    client = Client()
    client.list_mlb_matches("2025-04-01", limit=200)

    assert client.calls == [
        {
            "gameDate": "2025-04-01",
            "limit": 200,
            "asOf": None,
            "stored": True,
        }
    ]


def test_historical_resource_surfaces_are_real_cached_stored_endpoints():
    class Client:
        def __init__(self):
            self.requests = []
            self.fallbacks = []

        def get_mlb_match_resource(
            self, match_id, resource, *, game_date=None, as_of=None
        ):
            self.fallbacks.append((match_id, resource, game_date, as_of))
            return {"data": {}, "meta": {}, "error": None}

        def _request(self, endpoint, params):
            self.requests.append((endpoint, dict(params)))
            meta = {
                "source": "bigballsdata",
                "asOfUtc": "2026-07-27T22:00:00Z",
                "confirmed": True,
            }
            if endpoint.endswith("/lineups"):
                return (
                    {
                        "data": {
                            "home": {
                                "startingPitcher": {
                                    "id": "hp",
                                    "name": "Home Pitcher",
                                    "confirmed": True,
                                },
                                "lineup": [{"id": "h1", "slot": 1}],
                                "confirmed": True,
                            },
                            "away": {
                                "startingPitcher": {
                                    "id": "ap",
                                    "name": "Away Pitcher",
                                    "confirmed": True,
                                },
                                "lineup": [{"id": "a1", "slot": 1}],
                                "confirmed": True,
                            },
                        },
                        "meta": meta,
                        "error": None,
                    },
                    {},
                )
            return (
                {
                    "data": {
                        "home": {
                            "bullpen": {"era": 3.1},
                            "teamStats": {"record": "60-40"},
                        },
                        "away": {
                            "bullpen": {"era": 4.2},
                            "teamStats": {"record": "50-50"},
                        },
                    },
                    "meta": meta,
                    "error": None,
                },
                {},
            )

        @staticmethod
        def _transport(
            headers, *, requested_date=None, requested_as_of=None, endpoint=None
        ):
            return {
                "requestedDate": requested_date,
                "requestedAsOfUtc": requested_as_of,
                "endpoint": endpoint,
            }

    entrypoint.install_historical_resource_surfaces(Client)
    client = Client()
    kwargs = {
        "game_date": "2026-07-27",
        "as_of": "2026-07-27T22:15:00Z",
    }

    pitchers = client.get_mlb_match_resource("match id", "pitchers", **kwargs)
    lineups = client.get_mlb_match_resource("match id", "lineups", **kwargs)
    bullpens = client.get_mlb_match_resource("match id", "bullpens", **kwargs)
    teams = client.get_mlb_match_resource("match id", "team_context", **kwargs)

    assert pitchers["data"]["home"]["name"] == "Home Pitcher"
    assert lineups["data"]["away"]["players"][0]["id"] == "a1"
    assert bullpens["data"]["home"]["era"] == 3.1
    assert teams["data"]["away"]["record"] == "50-50"
    assert client.requests == [
        (
            "/v1/stored/matches/match%20id/lineups",
            {
                "sport": "baseball",
                "league": "mlb",
                "date": "2026-07-27",
                "as_of": "2026-07-27T22:15:00Z",
            },
        ),
        (
            "/v1/stored/matches/match%20id/stats",
            {
                "sport": "baseball",
                "league": "mlb",
                "date": "2026-07-27",
                "as_of": "2026-07-27T22:15:00Z",
            },
        ),
    ]
    assert client.fallbacks == []


def test_unavailable_point_in_time_resource_is_not_fabricated():
    class Client:
        def get_mlb_match_resource(self, *_args, **_kwargs):
            return {"data": {}}

    entrypoint.install_historical_resource_surfaces(Client)
    client = Client()

    with pytest.raises(
        backfill.BBSClientError,
        match="BBS_HISTORICAL_INJURIES_POINT_IN_TIME_UNAVAILABLE",
    ):
        client.get_mlb_match_resource(
            "m1",
            "injuries",
            game_date="2026-07-27",
            as_of="2026-07-27T22:15:00Z",
        )


def test_coverage_window_reverses_only_canonical_traversal_order():
    module = SimpleNamespace(
        _load_canonical_games=lambda _state, _s3: [
            {"officialGamePk": "1", "slateDateEt": "2025-04-01"},
            {"officialGamePk": "2", "slateDateEt": "2026-07-27"},
        ]
    )

    entrypoint.install_newest_coverage_window(module)
    rows = module._load_canonical_games({}, object())

    assert [row["officialGamePk"] for row in rows] == ["2", "1"]


def test_diagnostics_publish_only_shapes_counts_and_error_names(tmp_path):
    module = None

    def crosswalk(provider_rows, canonical_games, **_kwargs):
        return {
            "acceptedCount": 1,
            "quarantinedCount": 0,
            "accepted": {},
            "quarantined": [],
        }

    def snapshot(*_args, **_kwargs):
        return {
            "trainingEligible": False,
            "eligibilityErrors": ["pitchers_source_effective_time_missing"],
        }

    def run(*_args, **_kwargs):
        module.crosswalk_provider_rows(
            [{"id": "opaque-row"}],
            [
                {"slateDateEt": "2026-07-27", "officialGamePk": "1"},
                {"slateDateEt": "2026-07-27", "officialGamePk": "2"},
            ],
        )
        module.build_training_snapshot(
            None,
            None,
            None,
            {
                "lineups": {
                    "data": {"home": {"players": [{"id": "not-emitted"}]}},
                    "meta": {},
                    "error": None,
                },
                "injuries": {
                    "data": None,
                    "meta": {},
                    "error": "BBS_HISTORICAL_INJURIES_POINT_IN_TIME_UNAVAILABLE",
                },
            },
        )
        return {
            "ok": False,
            "selectedGameCount": 2,
            "blockers": ["current_batch_added_zero_training_eligible_rows"],
        }

    module = SimpleNamespace(
        crosswalk_provider_rows=crosswalk,
        build_training_snapshot=snapshot,
        run=run,
    )
    entrypoint.install_safe_diagnostics(module)
    output = tmp_path / "report.json"

    report = module.run(output=output)
    durable = json.loads(output.read_text())

    assert report["providerRowsReturned"] == 1
    assert report["acceptedCrosswalkCount"] == 1
    assert report["unmatchedCanonicalGameCount"] == 1
    assert report["eligibilityErrorCounts"] == {
        "pitchers_source_effective_time_missing": 1
    }
    assert report["resourceErrorCounts"] == {
        "injuries:BBS_HISTORICAL_INJURIES_POINT_IN_TIME_UNAVAILABLE": 1
    }
    assert report["resourceDataShapes"]["lineups"]["home"] == {
        "players": "array[1]"
    }
    assert report["diagnosticsContainProviderValues"] is False
    durable_text = output.read_text()
    assert "opaque-row" not in durable_text
    assert "not-emitted" not in durable_text
    assert durable == report
