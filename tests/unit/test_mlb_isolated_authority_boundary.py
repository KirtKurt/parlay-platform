from __future__ import annotations

from scripts import verify_mlb_deploy_identity as deploy_identity


class _LambdaClient:
    def __init__(self, functions: list[dict]) -> None:
        self._functions = functions

    def list_functions(self, **_kwargs) -> dict:
        return {"Functions": self._functions}


def _isolated_function() -> dict:
    return {
        "FunctionName": (
            "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234"
        ),
        "FunctionArn": (
            "arn:aws:lambda:us-east-1:123456789012:function:"
            "parlay-platform-mlb-auto-llm-MLBAutoLLMFunction-AbCd1234"
        ),
        "Handler": "orchestrator.lambda_handler",
        "Environment": {
            "Variables": {
                "MLB_AUTO_TABLE": "isolated-table",
                "ODDS_API_KEY": "configured",
                "BBS_API_SECRET_ARN": (
                    "arn:aws:secretsmanager:us-east-1:123456789012:"
                    "secret:isolated-mlb-auto"
                ),
                "MLB_AUTO_FIRST_GAME_SAFETY_MINUTES": "10",
                "MLB_AUTO_BEDROCK_MODELS": "us.amazon.nova-lite-v1:0",
            }
        },
    }


def test_authorized_isolated_three_source_auto_is_outside_root_scan() -> None:
    function = _isolated_function()

    assert deploy_identity._is_authorized_isolated_three_source_auto(function) is True
    assert deploy_identity._root_authority_lambda_functions(_LambdaClient([function])) == []


def test_isolated_lookalike_with_root_authority_table_is_rejected() -> None:
    function = _isolated_function()
    function["Environment"]["Variables"]["SNAPSHOTS_TABLE"] = "root-snapshots"

    assert deploy_identity._is_authorized_isolated_three_source_auto(function) is False
    assert deploy_identity._root_authority_lambda_functions(_LambdaClient([function])) == [function]


def test_isolated_lookalike_without_secret_manager_arn_is_rejected() -> None:
    function = _isolated_function()
    function["Environment"]["Variables"]["BBS_API_SECRET_ARN"] = "plain-text-key"

    assert deploy_identity._is_authorized_isolated_three_source_auto(function) is False
