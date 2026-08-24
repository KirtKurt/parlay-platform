from __future__ import annotations

from mlb_auto_llm import model_gateway


class _Runtime:
    def __init__(self, expected_model="us.amazon.nova-lite-v1:0"):
        self.expected_model = expected_model

    def converse(self, **kwargs):
        assert kwargs["modelId"] == self.expected_model
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
                    "inferenceProfileId": "global.xai.grok-4.6",
                },
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
                    "modelId": "meta.llama3-8b-instruct-v1:0",
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
    monkeypatch.setattr(model_gateway, "_RUNTIME_CLIENTS", {})
    monkeypatch.setattr(model_gateway, "_CONTROL_CLIENTS", {})
    model_gateway.discovered_models.cache_clear()


def test_converse_path_returns_nonempty_text() -> None:
    result = model_gateway.invoke_text(
        "us.amazon.nova-lite-v1:0",
        "Return only OK",
        client=_Runtime(),
        max_tokens=16,
    )
    assert result["text"] == "OK"
    assert result["modelId"] == "us.amazon.nova-lite-v1:0"
    assert result["region"] == "us-east-1"
    assert result["endpointFamily"] == "bedrock-runtime-converse"


def test_regional_route_uses_target_region_and_strips_prefix(monkeypatch) -> None:
    created = []

    def fake_client(service, *, region_name, config):
        created.append((service, region_name, config))
        assert service == "bedrock-runtime"
        return _Runtime("us.meta.llama4-scout-17b-instruct-v1:0")

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway.boto3, "client", fake_client)
    result = model_gateway.invoke_text(
        "us-west-2::us.meta.llama4-scout-17b-instruct-v1:0",
        "Return only OK",
        max_tokens=8,
    )
    assert result["text"] == "OK"
    assert result["region"] == "us-west-2"
    assert result["modelId"] == "us.meta.llama4-scout-17b-instruct-v1:0"
    assert created[0][0:2] == ("bedrock-runtime", "us-west-2")


def test_chain_falls_through_to_next_model(monkeypatch) -> None:
    attempts = []

    def fake_invoke(route_id, prompt, **kwargs):
        attempts.append(route_id)
        if route_id == "global.xai.grok-4.6":
            raise RuntimeError("quota")
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-runtime-converse",
            "modelId": "global.openai.gpt-5.6-luna",
            "region": "us-east-1",
        }

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text(
        "Return only OK",
        ["global.xai.grok-4.6", "global.openai.gpt-5.6-luna"],
    )
    assert result["ok"] is True
    assert result["modelId"] == "global.openai.gpt-5.6-luna"
    assert result["routeId"] == "global.openai.gpt-5.6-luna"
    assert attempts == ["global.xai.grok-4.6", "global.openai.gpt-5.6-luna"]
    assert len(result["errorsBeforeSuccess"]) == 1


def test_preferred_success_is_reused_first(monkeypatch) -> None:
    attempts = []

    def fake_invoke(route_id, prompt, **kwargs):
        attempts.append(route_id)
        region, model_id = model_gateway._split_route(route_id)
        return {
            "text": "OK",
            "usage": {},
            "endpointFamily": "bedrock-runtime-converse",
            "modelId": model_id,
            "region": region,
        }

    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", "us-west-2::second")
    monkeypatch.setattr(model_gateway, "invoke_text", fake_invoke)
    result = model_gateway.invoke_chain_text("x", ["first", "us-west-2::second"])
    assert result["ok"] is True
    assert result["modelId"] == "second"
    assert result["region"] == "us-west-2"
    assert attempts == ["us-west-2::second"]


def test_reset_model_state_clears_warm_cooldowns(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "_PREFERRED_MODEL", "model-a")
    model_gateway._MODEL_FAILURE_UNTIL["model-a"] = 9999999999.0
    model_gateway.reset_model_state()
    assert model_gateway._PREFERRED_MODEL is None
    assert model_gateway._MODEL_FAILURE_UNTIL == {}


def test_live_discovery_keeps_only_active_text_routes(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(model_gateway, "configured_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(model_gateway, "_CONTROL_CLIENTS", {"us-east-1": _Control()})
    try:
        models = model_gateway.discovered_models()
    finally:
        model_gateway.discovered_models.cache_clear()
    assert "global.xai.grok-4.6" in models
    assert "meta.llama3-8b-instruct-v1:0" in models
    assert "us.example.small-v1:0" in models
    assert "us.example.inactive-v1:0" not in models
    assert "example.retired-v1:0" not in models


def test_multi_region_discovery_prefixes_alternate_region(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(
        model_gateway, "configured_regions", lambda: ["us-east-1", "us-west-2"]
    )
    monkeypatch.setattr(
        model_gateway,
        "_CONTROL_CLIENTS",
        {"us-east-1": _Control(), "us-west-2": _Control()},
    )
    try:
        models = model_gateway.discovered_models()
    finally:
        model_gateway.discovered_models.cache_clear()
    assert "global.xai.grok-4.6" in models
    assert "us-west-2::global.xai.grok-4.6" in models
    assert "us-west-2::meta.llama3-8b-instruct-v1:0" in models


def test_configured_models_put_current_profiles_before_exhausted_nova(monkeypatch) -> None:
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
    assert models[:5] == [
        "global.xai.grok-4.6",
        "global.openai.gpt-5.6-luna",
        "global.openai.gpt-5.6-terra",
        "global.anthropic.claude-fable-5",
        "global.anthropic.claude-sonnet-5",
    ]
    assert models.count("amazon.nova-micro-v1:0") == 1
    assert "us.example.small-v1:0" in models
    assert models.index("global.xai.grok-4.6") < models.index(
        "amazon.nova-micro-v1:0"
    )
    assert "meta.llama3-2-1b-instruct-v1:0" not in models
    assert "meta.llama3-2-3b-instruct-v1:0" not in models


def test_daily_token_failure_enters_cooldown(monkeypatch) -> None:
    attempts = []

    def fake_invoke(route_id, prompt, **kwargs):
        attempts.append(route_id)
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
