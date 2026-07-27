from __future__ import annotations

import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[2] / "scripts" / "resolve_mlb_historical_artifacts_bucket.py"
SPEC = importlib.util.spec_from_file_location("bucket_resolver", PATH)
subject = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(subject)


class Client:
    def __init__(self, result=None, error=None):
        self.result = result or {}
        self.error = error

    def describe_stacks(self, **kwargs):
        if self.error:
            raise self.error
        return self.result

    def get_function_configuration(self, **kwargs):
        if self.error:
            raise self.error
        return self.result

    def list_buckets(self):
        if self.error:
            raise self.error
        return self.result


def test_explicit_override_is_authoritative():
    result = subject.resolve_bucket(
        explicit="bucket-explicit",
        cloudformation=Client(error=RuntimeError("unused")),
        lambda_client=Client(error=RuntimeError("unused")),
        s3=Client(error=RuntimeError("unused")),
    )
    assert result["ok"] is True
    assert result["bucketName"] == "bucket-explicit"
    assert result["authority"] == "EXPLICIT_ENV"


def test_cloudformation_output_is_used():
    result = subject.resolve_bucket(
        explicit=None,
        cloudformation=Client({"Stacks": [{"Outputs": [{"OutputKey": subject.OUTPUT_KEY, "OutputValue": "bucket-cf"}]}]}),
        lambda_client=Client(),
        s3=Client(),
    )
    assert result["bucketName"] == "bucket-cf"
    assert result["authority"] == "CLOUDFORMATION_OUTPUT"


def test_lambda_environment_fallback_is_used():
    result = subject.resolve_bucket(
        explicit=None,
        cloudformation=Client({"Stacks": [{"Outputs": [{"OutputKey": subject.FUNCTION_OUTPUT_KEY, "OutputValue": "optimizer"}]}]}),
        lambda_client=Client({"Environment": {"Variables": {"MLB_HISTORICAL_ARTIFACTS_BUCKET": "bucket-lambda"}}}),
        s3=Client(),
    )
    assert result["bucketName"] == "bucket-lambda"
    assert result["authority"] == "LAMBDA_ENVIRONMENT"


def test_unique_s3_prefix_fallback_survives_stack_failure():
    result = subject.resolve_bucket(
        explicit=None,
        cloudformation=Client(error=RuntimeError("stack unavailable")),
        lambda_client=Client(),
        s3=Client({"Buckets": [{"Name": subject.BUCKET_PREFIXES[0] + "abc"}, {"Name": "other"}]}),
    )
    assert result["ok"] is True
    assert result["authority"] == "S3_UNIQUE_PREFIX"


def test_ambiguous_s3_candidates_fail_closed():
    result = subject.resolve_bucket(
        explicit=None,
        cloudformation=Client({"Stacks": []}),
        lambda_client=Client(),
        s3=Client({"Buckets": [{"Name": subject.BUCKET_PREFIXES[0] + "a"}, {"Name": subject.BUCKET_PREFIXES[0] + "b"}]}),
    )
    assert result["ok"] is False
    assert result["blockers"] == ["historical_artifacts_bucket_ambiguous"]
