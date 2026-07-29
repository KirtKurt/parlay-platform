from __future__ import annotations

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
