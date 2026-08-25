from __future__ import annotations

import io
import json

from mlb_auto_llm import production_model_gateway as gateway


class _Control:
    def list_foundation_models(self, **kwargs):
        assert kwargs == {
            "byOutputModality": "TEXT",
            "byInferenceType": "ON_DEMAND",
        }
        return {
            "modelSummaries": [
                {
                    "modelId": "openai.gpt-oss-20b-1:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "amazon.titan-text-premier-v1:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "amazon.nova-lite-v1:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "anthropic.claude-haiku-v1:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "amazon.titan-embed-text-v2:0",
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelId": "amazon.retired-v1:0",
                    "modelLifecycle": {"status": "END_OF_LIFE"},
                },
            ]
        }

    def list_inference_profiles(self, **kwargs):
        assert kwargs == {"typeEquals": "SYSTEM_DEFINED", "maxResults": 1000}
        return {
            "inferenceProfileSummaries": [
                {
                    "status": "ACTIVE",
                    "inferenceProfileId": "us.amazon.nova-lite-v1:0",
                },
                {
                    "status": "ACTIVE",
                    "inferenceProfileId": "global.openai.gpt-5.6-luna",
                },
                {
                    "status": "INACTIVE",
                    "inferenceProfileId": "us.amazon.inactive-v1:0",
                },
            ]
        }


class _TitanRuntime:
    def __init__(self):
        self.invoke_calls = []

    def invoke_model(self, **kwargs):
        self.invoke_calls.append(kwargs)
        payload = {
            "inputTextTokenCount": 2,
            "results": [{"outputText": "OK", "tokenCount": 1}],
        }
        return {"body": io.BytesIO(json.dumps(payload).encode("utf-8"))}

    def converse(self, **kwargs):
        raise AssertionError("Titan Text must use InvokeModel, not Converse")


def _reset(monkeypatch) -> None:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr(gateway, "_PREFERRED_ROUTE", None)
    monkeypatch.setattr(gateway, "_ROUTE_FAILURE_UNTIL", {})
    gateway.mantle_models.cache_clear()
    gateway.runtime_models.cache_clear()


def test_mantle_catalog_comes_from_endpoint_native_models(monkeypatch) -> None:
    _reset(monkeypatch)
    calls = []
    monkeypatch.setattr(gateway.legacy, "configured_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(gateway.legacy, "_bearer_token", lambda: "token")

    def fake_http(url, **kwargs):
        calls.append((url, kwargs))
        return {
            "data": [
                {"id": "oss-gpt-20b"},
                {"id": "reasoning-large"},
                {"id": "image-model"},
            ]
        }

    monkeypatch.setattr(gateway, "_http_json", fake_http)
    assert gateway.mantle_models() == [
        "mantle::us-east-1::oss-gpt-20b",
        "mantle::us-east-1::reasoning-large",
    ]
    assert calls[0][0] == "https://bedrock-mantle.us-east-1.api.aws/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer token"


def test_mantle_token_failure_does_not_disable_runtime(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(
        gateway.legacy,
        "_bearer_token",
        lambda: (_ for _ in ()).throw(RuntimeError("token unavailable")),
    )
    assert gateway.mantle_models() == []


def test_runtime_catalog_excludes_mantle_only_marketplace_and_non_text_models(
    monkeypatch,
) -> None:
    _reset(monkeypatch)
    monkeypatch.setenv("MLB_AUTO_BEDROCK_RUNTIME_PROVIDERS", "amazon,openai")
    monkeypatch.setattr(gateway.legacy, "configured_regions", lambda: ["us-east-1"])
    monkeypatch.setattr(gateway.legacy, "_control_client", lambda region: _Control())
    models = gateway.runtime_models()
    assert "openai.gpt-oss-20b-1:0" in models
    assert "amazon.titan-text-premier-v1:0" in models
    assert "amazon.nova-lite-v1:0" in models
    assert "us.amazon.nova-lite-v1:0" in models
    assert "global.openai.gpt-5.6-luna" not in models
    assert "anthropic.claude-haiku-v1:0" not in models
    assert "amazon.titan-embed-text-v2:0" not in models
    assert "amazon.retired-v1:0" not in models


def test_mantle_route_uses_responses_endpoint(monkeypatch) -> None:
    _reset(monkeypatch)
    captured = {}
    monkeypatch.setattr(gateway.legacy, "_bearer_token", lambda: "token")

    def fake_http(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return {
            "output": [
                {"content": [{"type": "output_text", "text": "OK"}]}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    monkeypatch.setattr(gateway, "_http_json", fake_http)
    result = gateway.invoke_text(
        "mantle::us-east-1::oss-gpt-20b",
        "Return only OK",
        max_tokens=8,
    )
    assert result["text"] == "OK"
    assert result["endpointFamily"] == "bedrock-mantle-responses"
    assert captured["url"] == "https://bedrock-mantle.us-east-1.api.aws/v1/responses"
    assert captured["method"] == "POST"
    assert captured["payload"]["model"] == "oss-gpt-20b"
    assert captured["payload"]["store"] is False


def test_titan_route_uses_native_invoke_model(monkeypatch) -> None:
    _reset(monkeypatch)
    runtime = _TitanRuntime()
    result = gateway.invoke_text(
        "amazon.titan-text-premier-v1:0",
        "Return only OK",
        client=runtime,
        max_tokens=8,
    )
    assert result["text"] == "OK"
    assert result["endpointFamily"] == "bedrock-runtime-invoke-model-titan-text"
    assert runtime.invoke_calls[0]["modelId"] == "amazon.titan-text-premier-v1:0"
    request = json.loads(runtime.invoke_calls[0]["body"].decode("utf-8"))
    assert request["inputText"] == "Return only OK"
    assert request["textGenerationConfig"]["maxTokenCount"] == 8


def test_chain_is_bounded_and_does_not_bruteforce_catalog(monkeypatch) -> None:
    _reset(monkeypatch)
    attempts = []
    monkeypatch.setenv("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "2")
    monkeypatch.setattr(gateway, "mantle_models", lambda: [])
    monkeypatch.setattr(gateway, "runtime_models", lambda: [])

    def fail(route_id, prompt, **kwargs):
        attempts.append(route_id)
        raise RuntimeError("not available for this account")

    monkeypatch.setattr(gateway, "invoke_text", fail)
    result = gateway.invoke_chain_text("x", ["a", "b", "c", "d"])
    assert result["ok"] is False
    assert result["attemptedModelIds"] == ["a", "b"]
    assert attempts == ["a", "b"]


def test_permanent_denial_enters_long_cooldown(monkeypatch) -> None:
    _reset(monkeypatch)
    attempts = []
    monkeypatch.setenv("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "1")
    monkeypatch.setattr(gateway, "mantle_models", lambda: [])
    monkeypatch.setattr(gateway, "runtime_models", lambda: [])

    def fail(route_id, prompt, **kwargs):
        attempts.append(route_id)
        raise RuntimeError("INVALID_PAYMENT_INSTRUMENT")

    monkeypatch.setattr(gateway, "invoke_text", fail)
    first = gateway.invoke_chain_text("x", ["model-a"])
    second = gateway.invoke_chain_text("x", ["model-a"])
    assert first["attemptedModelIds"] == ["model-a"]
    assert second["attemptedModelIds"] == []
    assert any(
        row.get("errorCode") == "MODEL_COOLDOWN"
        for row in second.get("errors") or []
    )
    assert attempts == ["model-a"]


def test_configured_models_prefers_endpoint_native_mantle_catalog(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(
        gateway,
        "mantle_models",
        lambda: ["mantle::us-east-1::fast-luna", "mantle::us-east-1::large"],
    )
    monkeypatch.setattr(
        gateway,
        "runtime_models",
        lambda: [
            "openai.gpt-oss-20b-1:0",
            "amazon.titan-text-premier-v1:0",
            "amazon.nova-lite-v1:0",
        ],
    )
    monkeypatch.setenv(
        "MLB_AUTO_BEDROCK_MODELS",
        "global.openai.gpt-5.6-luna,openai.gpt-oss-20b-1:0",
    )
    models = gateway.configured_models()
    assert models[:2] == [
        "mantle::us-east-1::fast-luna",
        "mantle::us-east-1::large",
    ]
    assert "global.openai.gpt-5.6-luna" not in models
    assert models.count("openai.gpt-oss-20b-1:0") == 1



def test_explicit_max_attempts_override_is_bounded(monkeypatch) -> None:
    _reset(monkeypatch)
    attempts = []
    monkeypatch.setenv("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8")
    monkeypatch.setattr(gateway, "mantle_models", lambda: [])
    monkeypatch.setattr(gateway, "runtime_models", lambda: [])

    def fail(route_id, prompt, **kwargs):
        attempts.append(route_id)
        raise RuntimeError("unavailable")

    monkeypatch.setattr(gateway, "invoke_text", fail)
    result = gateway.invoke_chain_text(
        "x", ["a", "b", "c"], max_attempts=1
    )
    assert result["ok"] is False
    assert result["attemptedModelIds"] == ["a"]
    assert attempts == ["a"]


def test_explicit_empty_catalog_does_not_expand(monkeypatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(
        gateway,
        "configured_models",
        lambda: (_ for _ in ()).throw(
            AssertionError("explicit empty catalog must not expand")
        ),
    )
    monkeypatch.setattr(gateway, "mantle_models", lambda: [])
    monkeypatch.setattr(gateway, "runtime_models", lambda: [])
    result = gateway.invoke_chain_text("x", [], max_attempts=1)
    assert result["ok"] is False
    assert result["attemptedModelIds"] == []
