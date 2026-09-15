from __future__ import annotations

from scripts import verify_mlb_deploy_identity as deploy_identity
from scripts import repair_mlb_isolated_authority_boundary as repair_boundary


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
                "MLB_AUTO_TABLE": (
                    "parlay-platform-mlb-auto-llm-MLBAutoLLMTable-AbCd1234"
                ),
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


def test_deterministic_repair_is_noop_on_hardened_verifier(tmp_path) -> None:
    source = repair_boundary.VERIFIER.read_text(encoding="utf-8")
    verifier = tmp_path / "verify_mlb_deploy_identity.py"
    verifier.write_text(source, encoding="utf-8")

    assert repair_boundary.repair(verifier) is False
    assert verifier.read_text(encoding="utf-8") == source


def test_deterministic_repair_rejects_partial_hardened_verifier(tmp_path) -> None:
    source = repair_boundary.VERIFIER.read_text(encoding="utf-8")
    enforcement_expressions = (
        "ISOLATED_THREE_SOURCE_FUNCTION_NAME_PATTERN.fullmatch(name)",
        "handler in ISOLATED_THREE_SOURCE_HANDLERS",
        "ISOLATED_THREE_SOURCE_TABLE_NAME_PATTERN.fullmatch(isolated_table)",
        "and forbidden_absent",
        "and unexpected_provider_authority_absent",
        "ISOLATED_THREE_SOURCE_SECRET_ARN_PATTERN.fullmatch(secret_arn)",
        "if target_is_unqualified\n                    else None",
    )
    for index, expression in enumerate(enforcement_expressions):
        verifier = tmp_path / f"partial_{index}.py"
        verifier.write_text(source.replace(expression, "", 1), encoding="utf-8")

        try:
            repair_boundary.repair(verifier)
        except RuntimeError as error:
            assert "hardened isolated authority contract is incomplete" in str(error)
        else:
            raise AssertionError(f"partial contract was accepted: {expression}")


def test_isolated_lookalike_with_any_root_authority_binding_is_rejected() -> None:
    for key in (
        "SNAPSHOTS_TABLE",
        "SIGNALS_TABLE",
        "SIGNAL_LEDGER_TABLE",
        "PREDICTIONS_TABLE",
        "OUTCOMES_TABLE",
        "MLB_ML_ARTIFACTS_BUCKET",
    ):
        function = _isolated_function()
        function["Environment"]["Variables"][key] = "root-authority"

        assert (
            deploy_identity._is_authorized_isolated_three_source_auto(function)
            is False
        )
        assert deploy_identity._root_authority_lambda_functions(
            _LambdaClient([function])
        ) == [function]


def test_isolated_lookalike_without_secret_manager_arn_is_rejected() -> None:
    function = _isolated_function()
    function["Environment"]["Variables"]["BBS_API_SECRET_ARN"] = "plain-text-key"

    assert deploy_identity._is_authorized_isolated_three_source_auto(function) is False
