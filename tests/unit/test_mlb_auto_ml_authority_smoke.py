from __future__ import annotations

from mlb_auto_llm import ml_authority_smoke


def test_deployment_smoke_is_bedrock_only_on_success(monkeypatch) -> None:
    monkeypatch.setattr(
        ml_authority_smoke,
        "_bedrock_smoke",
        lambda event, context: {
            "ok": True,
            "responseNonEmpty": True,
            "modelId": "endpoint-model",
        },
    )
    result = ml_authority_smoke.lambda_handler({}, None)
    assert result["ok"] is True
    assert result["decisionAuthority"] == "BEDROCK_LLM"
    assert result["bedrockAvailable"] is True
    assert result["mlFallbackAttempted"] is False


def test_deployment_smoke_surfaces_bedrock_errors_without_ml_call(monkeypatch) -> None:
    errors = [{"errorCode": "AccessDeniedException", "message": "denied"}]
    monkeypatch.setattr(
        ml_authority_smoke,
        "_bedrock_smoke",
        lambda event, context: {"ok": False, "errors": errors},
    )
    result = ml_authority_smoke.lambda_handler({}, None)
    assert result["ok"] is False
    assert result["errors"] == errors
    assert result["decisionAuthority"] == "BEDROCK_LLM"
    assert result["bedrockAvailable"] is False
    assert result["mlFallbackAttempted"] is False
