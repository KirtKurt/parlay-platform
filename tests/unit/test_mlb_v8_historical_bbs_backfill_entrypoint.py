from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

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


def test_diagnostics_publish_only_counts_and_error_names(tmp_path):
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
    module.crosswalk_provider_rows(
        [{"id": "provider-secret-row-not-emitted"}],
        [
            {"slateDateEt": "2026-07-27", "officialGamePk": "1"},
            {"slateDateEt": "2026-07-27", "officialGamePk": "2"},
        ],
    )
    module.build_training_snapshot()
    output = tmp_path / "report.json"

    report = module.run(output=output)
    durable = json.loads(output.read_text())

    assert report["providerRowsReturned"] == 1
    assert report["acceptedCrosswalkCount"] == 1
    assert report["unmatchedCanonicalGameCount"] == 1
    assert report["eligibilityErrorCounts"] == {
        "pitchers_source_effective_time_missing": 1
    }
    assert report["diagnosticsContainProviderValues"] is False
    assert "provider-secret-row-not-emitted" not in output.read_text()
    assert durable == report
