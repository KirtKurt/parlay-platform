from __future__ import annotations

from mlb_auto_llm import bedrock_smoke


def _install_catalog(monkeypatch, *, mantle=None, runtime=None, catalog=None):
    monkeypatch.setattr(bedrock_smoke, "mantle_models", lambda: list(mantle or []))
    monkeypatch.setattr(bedrock_smoke, "runtime_models", lambda: list(runtime or []))
    monkeypatch.setattr(
        bedrock_smoke,
        "configured_models",
        lambda: list(catalog or []),
    )


def test_smoke_uses_bounded_diversified_failover(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "4")
    monkeypatch.setattr(
        bedrock_smoke,
        "reset_model_state",
        lambda **kwargs: captured.setdefault("reset", kwargs),
    )
    _install_catalog(
        monkeypatch,
        mantle=["mantle::us-east-1::fast-model"],
        runtime=[
            "openai.gpt-oss-20b-1:0",
            "global.amazon.nova-2-lite-v1:0",
            "us.amazon.nova-2-lite-v1:0",
        ],
        catalog=[
            "mantle::us-east-1::fast-model",
            "openai.gpt-oss-20b-1:0",
            "global.amazon.nova-2-lite-v1:0",
            "us.amazon.nova-2-lite-v1:0",
            "configured-extra",
        ],
    )

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["kwargs"] = kwargs
        return {
            "ok": True,
            "text": "OK",
            "routeId": "mantle::us-east-1::fast-model",
            "region": "us-east-1",
            "modelId": "fast-model",
            "endpointFamily": "bedrock-mantle-responses",
            "attemptedModelIds": [
                "us.amazon.nova-2-lite-v1:0",
                "mantle::us-east-1::fast-model",
            ],
            "errorsBeforeSuccess": [
                {
                    "routeId": "us.amazon.nova-2-lite-v1:0",
                    "errorCode": "ThrottlingException",
                }
            ],
        }

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)

    assert result["ok"] is True
    assert result["responseNonEmpty"] is True
    assert captured["models"] == [
        "us.amazon.nova-2-lite-v1:0",
        "mantle::us-east-1::fast-model",
        "configured-extra",
        "global.amazon.nova-2-lite-v1:0",
    ]
    assert captured["kwargs"]["max_attempts"] == 4
    assert captured["reset"] == {
        "clear_discovery": True,
        "clear_failures": False,
    }
    assert result["configuredModelCount"] == 4
    assert result["configuredRouteCatalogCount"] == 5
    assert result["routeAttemptLimit"] == 4
    assert result["smokeRouteLimit"] == 4
    assert result["failoverEnabled"] is True
    assert result["mantleModelCount"] == 1
    assert result["runtimeModelCount"] == 3
    assert result["routeSelectionPolicy"] == (
        "round-robin-cross-region-runtime,mantle,remaining-catalog"
    )


def test_smoke_preserves_cross_region_runtime_priority(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "3")
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    runtime = [
        "openai.gpt-oss-20b-1:0",
        "global.amazon.nova-2-lite-v1:0",
        "us.amazon.nova-2-lite-v1:0",
    ]
    _install_catalog(monkeypatch, runtime=runtime, catalog=runtime)

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        return {
            "ok": True,
            "text": "OK",
            "routeId": models[0],
            "region": "us-east-1",
            "modelId": models[0],
            "endpointFamily": "bedrock-runtime-converse",
            "attemptedModelIds": [models[0]],
        }

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)

    assert result["ok"] is True
    assert captured["models"] == [
        "us.amazon.nova-2-lite-v1:0",
        "global.amazon.nova-2-lite-v1:0",
        "openai.gpt-oss-20b-1:0",
    ]


def test_smoke_default_limit_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", raising=False)
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    runtime = [f"route-{index}" for index in range(40)]
    _install_catalog(monkeypatch, runtime=runtime, catalog=runtime)
    captured = {}

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["max_attempts"] = kwargs["max_attempts"]
        return {"ok": False, "attemptedModelIds": list(models), "errors": []}

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)

    assert result["ok"] is False
    assert len(captured["models"]) == bedrock_smoke.DEFAULT_SMOKE_ROUTE_LIMIT
    assert captured["max_attempts"] == bedrock_smoke.DEFAULT_SMOKE_ROUTE_LIMIT
    assert result["configuredRouteCatalogCount"] == 40


def test_smoke_route_limit_is_hard_capped(monkeypatch) -> None:
    monkeypatch.setenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "999")
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    runtime = [f"route-{index}" for index in range(80)]
    _install_catalog(monkeypatch, runtime=runtime, catalog=runtime)
    captured = {}

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["max_attempts"] = kwargs["max_attempts"]
        return {"ok": False, "attemptedModelIds": list(models), "errors": []}

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)

    assert result["ok"] is False
    assert len(captured["models"]) == bedrock_smoke.MAX_SMOKE_ROUTE_LIMIT
    assert captured["max_attempts"] == bedrock_smoke.MAX_SMOKE_ROUTE_LIMIT


def test_smoke_empty_catalog_fails_fast(monkeypatch) -> None:
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    _install_catalog(monkeypatch)
    captured = {}

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["max_attempts"] = kwargs["max_attempts"]
        return {"ok": False, "attemptedModelIds": [], "errors": []}

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)

    assert result["ok"] is False
    assert captured == {"models": [], "max_attempts": 1}
    assert result["routeAttemptLimit"] == 1
    assert result["failoverEnabled"] is False
