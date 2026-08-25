from __future__ import annotations

from mlb_auto_llm import ml_authority_smoke


def test_smoke_prefers_bedrock_success(monkeypatch) -> None:
    monkeypatch.setattr(
        ml_authority_smoke.bedrock_smoke,
        "lambda_handler",
        lambda event, context: {
            "ok": True,
            "responseNonEmpty": True,
            "modelId": "endpoint-model",
        },
    )
    monkeypatch.setattr(
        ml_authority_smoke.ml_authority,
        "smoke",
        lambda: (_ for _ in ()).throw(
            AssertionError("ML fallback must not run after Bedrock success")
        ),
    )
    result = ml_authority_smoke.lambda_handler({}, None)
    assert result["ok"] is True
    assert result["decisionAuthority"] == "BEDROCK_LLM"
    assert result["bedrockAvailable"] is True
    assert result["mlFallbackAttempted"] is False


def test_smoke_uses_ml_after_bounded_bedrock_failure(monkeypatch) -> None:
    errors = [{"errorCode": "MODEL_UNAVAILABLE"}]
    monkeypatch.setattr(
        ml_authority_smoke.bedrock_smoke,
        "lambda_handler",
        lambda event, context: {"ok": False, "errors": errors},
    )
    monkeypatch.setattr(
        ml_authority_smoke.ml_authority,
        "smoke",
        lambda: {
            "ok": True,
            "responseNonEmpty": True,
            "modelId": "ranked-ensemble",
            "decisionAuthority": "AWS_ML_RANKED_ENSEMBLE",
        },
    )
    result = ml_authority_smoke.lambda_handler({}, None)
    assert result["ok"] is True
    assert result["decisionAuthority"] == "AWS_ML_RANKED_ENSEMBLE"
    assert result["bedrockAvailable"] is False
    assert result["mlFallbackAttempted"] is True
    assert result["bedrockErrors"] == errors
