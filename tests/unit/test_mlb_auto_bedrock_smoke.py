from __future__ import annotations

from mlb_auto_llm import bedrock_smoke


def test_smoke_uses_one_runtime_route(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "1")
    monkeypatch.setattr(
        bedrock_smoke,
        "reset_model_state",
        lambda **kwargs: captured.setdefault("reset", kwargs),
    )
    monkeypatch.setattr(
        bedrock_smoke,
        "runtime_models",
        lambda: ["runtime-a", "runtime-b"],
    )

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["kwargs"] = kwargs
        return {
            "ok": True,
            "text": "OK",
            "routeId": "runtime-a",
            "region": "us-east-1",
            "modelId": "runtime-a",
            "endpointFamily": "bedrock-runtime-converse",
            "attemptedModelIds": ["runtime-a"],
        }

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)
    assert result["ok"] is True
    assert result["responseNonEmpty"] is True
    assert captured["models"] == ["runtime-a"]
    assert captured["kwargs"]["max_attempts"] == 1
    assert result["mantleModelCount"] == 0
    assert result["runtimeModelCount"] == 2


def test_smoke_empty_runtime_catalog_fails_fast(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    monkeypatch.setattr(bedrock_smoke, "runtime_models", lambda: [])
    captured = {}

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["max_attempts"] = kwargs["max_attempts"]
        return {"ok": False, "attemptedModelIds": [], "errors": []}

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)
    assert result["ok"] is False
    assert captured == {"models": [], "max_attempts": 1}
