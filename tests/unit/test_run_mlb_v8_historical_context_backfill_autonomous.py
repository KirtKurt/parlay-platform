from types import SimpleNamespace

from botocore.exceptions import ClientError

import run_mlb_v8_historical_context_backfill_autonomous as entrypoint


def _missing_stack():
    return ClientError(
        {
            "Error": {
                "Code": "ValidationError",
                "Message": "Stack with id fundamentals does not exist",
            }
        },
        "DescribeStacks",
    )


def test_missing_optional_fundamentals_stack_uses_historical_bucket():
    calls = []

    def outputs(_cf, stack_name):
        calls.append(stack_name)
        if stack_name == "fundamentals":
            raise _missing_stack()
        return {
            "HistoricalOptimizerFunctionName": "optimizer",
            "HistoricalArtifactsBucketName": "versioned-history-bucket",
        }

    module = SimpleNamespace(_outputs=outputs)
    entrypoint.install_artifact_bucket_alias(
        module,
        historical_stack="historical",
        fundamentals_stack="fundamentals",
    )

    value = module._outputs(object(), "fundamentals")

    assert calls == ["fundamentals", "historical"]
    assert value["FundamentalsArtifactsBucketName"] == "versioned-history-bucket"
    assert value["V8ContextArtifactsBucketResolution"] == entrypoint.VERSION


def test_existing_fundamentals_bucket_remains_authoritative():
    module = SimpleNamespace(
        _outputs=lambda _cf, _stack: {
            "FundamentalsArtifactsBucketName": "dedicated-bucket"
        }
    )
    entrypoint.install_artifact_bucket_alias(
        module,
        historical_stack="historical",
        fundamentals_stack="fundamentals",
    )

    value = module._outputs(object(), "fundamentals")

    assert value["FundamentalsArtifactsBucketName"] == "dedicated-bucket"


def test_non_missing_stack_error_fails_closed():
    denied = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}},
        "DescribeStacks",
    )
    module = SimpleNamespace(
        _outputs=lambda _cf, _stack: (_ for _ in ()).throw(denied)
    )
    entrypoint.install_artifact_bucket_alias(
        module,
        historical_stack="historical",
        fundamentals_stack="fundamentals",
    )

    try:
        module._outputs(object(), "fundamentals")
    except ClientError as exc:
        assert exc is denied
    else:
        raise AssertionError("AccessDenied was incorrectly converted to a fallback")


def _projection_resources(*, verified=True):
    def envelope(mode):
        return {
            "data": {},
            "meta": {
                "complete": True,
                "authoritative": True,
                "pointInTimeProjectionVerified": verified,
                "targetIdentityMode": mode,
                "derivationVersion": entrypoint.context_source.VERSION,
                "asOfUtc": "2026-06-30T23:59:59+00:00",
            },
            "error": None,
        }

    return {
        "pitchers": envelope("STRICTLY_PRIOR_ROTATION_PROJECTION"),
        "lineups": envelope("STRICTLY_PRIOR_LINEUP_PROJECTION"),
    }


def _normalized_projection(*, missing_domains=None):
    lineups = {}
    for side in ("away", "home"):
        lineups[side] = {
            "confirmed": False,
            "players": [
                {"slot": index, "id": f"{side}-{index}"}
                for index in range(1, 10)
            ],
        }
    return {
        "coverage": {
            "trainingEligible": False,
            "missingDomains": list(missing_domains or []),
            "confirmedLineups": False,
            "confirmedStarters": False,
        },
        "pitchers": {
            "away": {"id": "away-starter", "confirmed": False},
            "home": {"id": "home-starter", "confirmed": False},
        },
        "lineups": lineups,
    }


def _projection_module(*, extra_errors=None):
    errors = [
        "confirmed_lineups_missing",
        "confirmed_starters_missing",
        *(extra_errors or []),
    ]

    def base_snapshot(*_args, **_kwargs):
        return {
            "authority": entrypoint.official.AUTHORITY,
            "pointInTimeVerified": True,
            "sameDayResultsExcluded": True,
            "targetGameOutcomeUsed": False,
            "selectionUsedOutcomes": False,
            "productionAuthorityChanged": False,
            "trainingEligible": False,
            "eligibilityErrors": errors,
        }

    return SimpleNamespace(
        build_training_snapshot=base_snapshot,
        overlay=SimpleNamespace(snapshot_fingerprint=lambda value: "fingerprint"),
    )


def test_verified_strictly_prior_projections_replace_live_confirmation_requirement():
    module = _projection_module()
    entrypoint.install_verified_projection_eligibility(module)

    value = module.build_training_snapshot(
        {},
        {},
        _normalized_projection(),
        _projection_resources(),
    )

    proof = value["historicalProjectionEligibility"]
    assert value["trainingEligible"] is True
    assert value["eligibilityErrors"] == []
    assert value["fingerprint"] == "fingerprint"
    assert proof["accepted"] is True
    assert proof["pitcherProjectionVerified"] is True
    assert proof["lineupProjectionVerified"] is True
    assert proof["starterStructureVerified"] is True
    assert proof["lineupStructureVerified"] is True
    assert proof["targetGameOutcomeExcluded"] is True
    assert proof["sameDayResultsExcluded"] is True
    assert proof["productionAuthorityUnchanged"] is True


def test_unverified_projection_remains_training_ineligible():
    module = _projection_module()
    entrypoint.install_verified_projection_eligibility(module)

    value = module.build_training_snapshot(
        {},
        {},
        _normalized_projection(),
        _projection_resources(verified=False),
    )

    assert value["trainingEligible"] is False
    assert value["historicalProjectionEligibility"]["accepted"] is False
    assert value["eligibilityErrors"] == [
        "confirmed_lineups_missing",
        "confirmed_starters_missing",
    ]


def test_projection_does_not_hide_other_missing_domains_or_time_errors():
    module = _projection_module(extra_errors=["bullpens"])
    entrypoint.install_verified_projection_eligibility(module)

    value = module.build_training_snapshot(
        {},
        {},
        _normalized_projection(missing_domains=["bullpens"]),
        _projection_resources(),
    )

    assert value["trainingEligible"] is False
    assert value["historicalProjectionEligibility"]["accepted"] is False
    assert "bullpens" in value["eligibilityErrors"]


def test_projection_requires_complete_unique_starter_and_lineup_identity():
    module = _projection_module()
    entrypoint.install_verified_projection_eligibility(module)
    normalized = _normalized_projection()
    normalized["pitchers"]["home"]["id"] = None
    normalized["lineups"]["away"]["players"][8]["id"] = "away-8"

    value = module.build_training_snapshot(
        {},
        {},
        normalized,
        _projection_resources(),
    )

    proof = value["historicalProjectionEligibility"]
    assert value["trainingEligible"] is False
    assert proof["accepted"] is False
    assert proof["starterStructureVerified"] is False
    assert proof["lineupStructureVerified"] is False


def test_official_install_does_not_relabel_retired_compatibility_authority():
    entrypoint.retired_bbs_overlay.AUTHORITY = entrypoint.official.AUTHORITY
    module = SimpleNamespace(overlay=entrypoint.retired_bbs_overlay)

    value = entrypoint.restore_retired_overlay_authority(module)

    assert value is module
    assert (
        entrypoint.retired_bbs_overlay.AUTHORITY
        == entrypoint.RETIRED_BBS_AUTHORITY
    )
    assert (
        entrypoint.official.target_overlay.base.AUTHORITY
        == entrypoint.RETIRED_BBS_AUTHORITY
    )
