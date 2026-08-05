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
