from __future__ import annotations

from scripts import verify_mlb_deploy_identity as verifier


class _LambdaClient:
    def __init__(self, functions: list[dict]) -> None:
        self._functions = functions

    def list_functions(self, **_kwargs) -> dict:
        return {"Functions": self._functions}


def _function(*, handler: str = "orchestrator_v3.lambda_handler", root_table: bool = False) -> dict:
    environment = {
        "MLB_AUTO_TABLE": "isolated-table",
        "ODDS_API_KEY": "configured",
        "BBS_API_SECRET_ARN": (
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:isolated-mlb-auto"
        ),
        "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES": "10",
        "MLB_AUTO_BEDROCK_MODELS": "us.amazon.nova-lite-v1:0",
    }
    if root_table:
        environment["SNAPSHOTS_TABLE"] = "root-snapshots"
    return {
        "FunctionName": "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234",
        "FunctionArn": (
            "arn:aws:lambda:us-east-1:123456789012:function:"
            "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234"
        ),
        "Handler": handler,
        "Environment": {"Variables": environment},
    }


def test_orchestrator_v3_is_positive_isolated_boundary() -> None:
    function = _function()
    assert verifier._is_authorized_isolated_three_source_auto(function) is True
    assert verifier._root_authority_lambda_functions(_LambdaClient([function])) == []


def test_orchestrator_v3_with_root_table_is_not_exempted() -> None:
    function = _function(root_table=True)
    assert verifier._is_authorized_isolated_three_source_auto(function) is False
    assert verifier._root_authority_lambda_functions(_LambdaClient([function])) == [function]
