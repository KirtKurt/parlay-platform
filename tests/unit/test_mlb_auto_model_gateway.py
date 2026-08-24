from __future__ import annotations

from mlb_auto_llm import model_gateway


class _Runtime:
    def converse(self, **kwargs):
        assert kwargs["modelId"] == "us.amazon.nova-lite-v1:0"
        return {
            "output": {"message": {"content": [{"text": "OK"}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
        }


class _Control:
    def list_inference_profiles(self, **kwargs):
        assert kwargs == {"typeEquals": "SYSTEM_DEFINED", "maxResults": 1000}
        return {
            "inferenceProfileSummaries": [
                {
                    "status": "ACTIVE",
                    "inferenceProfileId": "us.example.small-v1:0",
                },
                {
                    "status": "INACTIVE",
                    "inferenceProfileId": "us.example.inactive-v1:0",
                },
            ]
        }

    def list_foundation_models(self, **kwargs):
        assert kwargs == {
            "byOutputModality": "TEXT",
            "byInferenceType": "ON_DEMAND",
        }
        return {
            "modelSummaries": [
                {
                    "modelId": "amazon.nova-micro-v1:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "example.retired-v1:0",
                    "modelLifecycle": {"status": "END_OF_LIFE"},
                },
            ]
        }


def _reset(monkeypatch) -> None:
    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", None)
    monkeypatch.setattr(model_gateway, "_MODEL_FAILURE_UNTIL", {})
    monkeypatch.setattr(model_gateway, "_RUNTIME_CLIENT", None)
    monkeypatch.setattr(model_gateway, "_CONTROL_CLIENT", None)


def test_converse_path_returns_nonempty_text() -> None:
    result = model_gateway.invoke_text(
        "us.amazon.nova-lite-v1:0",
        "Return only OK",
        client=_Runtime(),
        max_tokens=16,
    )

    assert result["text"] == "OK"
    assert result["endpointFamily"] == "bedrock-runtime-converse"


def test_chain_falls_through_to_next_model(monkeypatch) -> None:
    attempts = []

    def fake_invoke(model_id, prompt, **kwargs):
        attempts.append(model_id)
        if model_id == "openai.gpt-5.6-sol":
            raise RuntimeError("quota")
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-mantle-anthropic",
        }

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text(
        "Return only OK",
        ["openai.gpt-5.6-sol", "anthropic.claude-sonnet-4-6-v1"],
    )

    assert result["ok"] is True
    assert result["modelId"] == "anthropic.claude-sonnet-4-6-v1"
    assert attempts == ["openai.gpt-5.6-sol", "anthropic.claude-sonnet-4-6-v1"]
    assert len(result["errorsBeforeSuccess"]) == 1


def test_preferred_success_is_reused_first(monkeypatch) -> None:
    attempts = []

    def fake_invoke(model_id, prompt, **kwargs):
        attempts.append(model_id)
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-runtime-converse",
        }

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", "second")
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text("x", ["first", "second"])

    assert result["ok"] is True
    assert result["modelId"] == "second"
    assert attempts == ["second"]


def test_live_discovery_keeps_only_active_text_routes(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "_CONTROL_CLIENT", _Control())
    model_gateway.discovered_models.cache_clear()
    try:
        models = model_gateway.discovered_models()
    finally:
        model_gateway.discovered_models.cache_clear()

    assert "amazon.nova-micro-v1:0" in models
    assert "us.example.small-v1:0" in models
    assert "us.example.inactive-v1:0" not in models
    assert "example.retired-v1:0" not in models


def test_configured_models_put_direct_recovery_before_stale_env(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(
        model_gateway,
        "discovered_models",
        lambda: ["us.example.small-v1:0", "amazon.nova-micro-v1:0"],
    )
    monkeypatch.setenv(
        "MLB_AUTO_BEDROCK_MODELS",
        "openai.gpt-5.6-sol,amazon.nova-micro-v1:0",
    )

    models = model_gateway.configured_models()

    assert models[:4] == [
        "amazon.nova-micro-v1:0",
        "amazon.nova-lite-v1:0",
        "amazon.nova-pro-v1:0",
        "amazon.nova-2-lite-v1:0",
    ]
    assert models.count("amazon.nova-micro-v1:0") == 1
    assert "us.example.small-v1:0" in models
    assert models.index("us.example.small-v1:0") < models.index("openai.gpt-5.6-sol")


def test_daily_token_failure_enters_cooldown(monkeypatch) -> None:
    attempts = []

    def fake_invoke(model_id, prompt, **kwargs):
        attempts.append(model_id)
        raise RuntimeError("Too many tokens per day, please wait before trying again")

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    monkeypatch.setenv("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "1")

    first = model_gateway.invoke_chain_text("x", ["model-a"])
    second = model_gateway.invoke_chain_text("x", ["model-a"])

    assert first["ok"] is False
    assert first["attemptedModelIds"] == ["model-a"]
    assert second["ok"] is False
    assert second["attemptedModelIds"] == []
    assert any(
        row.get("errorCode") == "MODEL_COOLDOWN"
        for row in second.get("errors") or []
    )
    assert attempts == ["model-a"]
