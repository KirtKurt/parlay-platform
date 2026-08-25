from __future__ import annotations

import json
from io import BytesIO

import pytest

from mlb_auto_llm import ml_authority


class FakeCloudFormation:
    def __init__(self):
        self.calls = []

    def describe_stack_resource(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": "parlay-platform-dev-MLBV3ReadFunction-test"
            }
        }


class FakeLambda:
    def __init__(self, envelope=None, function_error=None):
        self.envelope = envelope or {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "ok": True,
                    "winner_predictions": [
                        {
                            "gamePk": "1",
                            "predictedWinner": "Home One",
                            "probability": 0.61,
                        }
                    ],
                }
            ),
        }
        self.function_error = function_error
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        response = {"Payload": BytesIO(json.dumps(self.envelope).encode("utf-8"))}
        if self.function_error:
            response["FunctionError"] = self.function_error
        return response


def install_clients(monkeypatch, *, lambda_client=None):
    cloudformation = FakeCloudFormation()
    lambda_client = lambda_client or FakeLambda()

    def client(service_name):
        if service_name == "cloudformation":
            return cloudformation
        if service_name == "lambda":
            return lambda_client
        raise AssertionError(f"unexpected boto3 client: {service_name}")

    monkeypatch.setattr(ml_authority.boto3, "client", client)
    monkeypatch.setattr(ml_authority, "_READ_FUNCTION_NAME", None)
    monkeypatch.setattr(ml_authority, "READ_STACK_NAME", "parlay-platform-dev")
    monkeypatch.setattr(ml_authority, "READ_LOGICAL_ID", "MLBV3ReadFunction")
    return cloudformation, lambda_client


def test_direct_read_resolves_read_lambda_and_preserves_get_semantics(monkeypatch):
    cloudformation, lambda_client = install_clients(monkeypatch)

    payload = ml_authority._direct_lambda_json(
        "/v1/mlb/game-winners",
        {"game_date_et": "2026-08-24", "limit": 64},
    )

    assert payload["ok"] is True
    assert cloudformation.calls == [
        {
            "StackName": "parlay-platform-dev",
            "LogicalResourceId": "MLBV3ReadFunction",
        }
    ]
    assert len(lambda_client.calls) == 1
    call = lambda_client.calls[0]
    assert call["FunctionName"] == "parlay-platform-dev-MLBV3ReadFunction-test"
    assert call["InvocationType"] == "RequestResponse"
    event = json.loads(call["Payload"].decode("utf-8"))
    assert event["httpMethod"] == "GET"
    assert event["requestContext"]["http"]["method"] == "GET"
    assert event["rawPath"] == "/v1/mlb/game-winners"
    assert event["queryStringParameters"] == {
        "game_date_et": "2026-08-24",
        "limit": "64",
    }


def test_direct_read_caches_resolved_function_name(monkeypatch):
    cloudformation, _ = install_clients(monkeypatch)
    first = ml_authority._resolve_read_function_name()
    second = ml_authority._resolve_read_function_name()
    assert first == second == "parlay-platform-dev-MLBV3ReadFunction-test"
    assert len(cloudformation.calls) == 1


def test_direct_read_fails_closed_on_lambda_function_error(monkeypatch):
    bad = FakeLambda(
        envelope={"errorMessage": "injected read failure"},
        function_error="Unhandled",
    )
    install_clients(monkeypatch, lambda_client=bad)

    with pytest.raises(RuntimeError, match="MLB_ML_DIRECT_READ_FUNCTION_ERROR"):
        ml_authority._direct_lambda_json("/v1/mlb/game-winners", {})


def test_http_json_uses_direct_read_inside_aws_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "mlb-auto-authority")
    observed = {}

    def direct(path, params=None):
        observed["path"] = path
        observed["params"] = params
        return {"ok": True, "winner_predictions": [{"gamePk": "1"}]}

    monkeypatch.setattr(ml_authority, "_direct_lambda_json", direct)
    result = ml_authority._http_json(
        "/v1/mlb/game-winners",
        {"game_date_et": "2026-08-24", "limit": 64},
    )

    assert result["ok"] is True
    assert observed == {
        "path": "/v1/mlb/game-winners",
        "params": {"game_date_et": "2026-08-24", "limit": 64},
    }
