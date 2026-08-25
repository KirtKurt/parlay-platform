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


def test_smoke_uses_diversified_configured_catalog(monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "3")
    monkeypatch.setattr(
        bedrock_smoke,
        "reset_model_state",
        lambda **kwargs: captured.setdefault("reset", kwargs),
    )
    _install_catalog(
        monkeypatch,
        mantle=["mantle-a"],
        runtime=["runtime-a", "runtime-b", "runtime-c"],
        catalog=["mantle-a", "runtime-a", "runtime-b", "runtime-c"],
    )

    def invoke(prompt, models, **kwargs):
        captured["models"] = list(models)
        captured["kwargs"] = kwargs
        return {
            "ok": True,
            "text": "OK",
            "routeId": "runtime-b",
            "region": "us-west-2",
            "modelId": "runtime-b",
            "endpointFamily": "bedrock-runtime-converse",
            "attemptedModelIds": ["mantle-a", "runtime-a", "runtime-b"],
            "errorsBeforeSuccess": [
                {"routeId": "mantle-a", "errorCode": "THROTTLED"},
                {"routeId": "runtime-a", "errorCode": "THROTTLED"},
            ],
        }

    monkeypatch.setattr(bedrock_smoke, "invoke_chain_text", invoke)
    result = bedrock_smoke.lambda_handler({}, None)
    assert result["ok"] is True
    assert result["responseNonEmpty"] is True
    assert captured["models"] == ["mantle-a", "runtime-a", "runtime-b"]
    assert captured["kwargs"]["max_attempts"] == 3
    assert captured["reset"] == {"clear_discovery": True, "clear_failures": False}
    assert result["mantleModelCount"] == 1
    assert result["runtimeModelCount"] == 3
    assert result["configuredRouteCatalogCount"] == 4
    assert result["smokeRouteLimit"] == 3
    assert result["attemptedModelIds"] == [
        "mantle-a",
        "runtime-a",
        "runtime-b",
    ]


def test_smoke_default_limit_is_bounded(monkeypatch) -> None:
    monkeypatch.delenv("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", raising=False)
    monkeypatch.setattr(bedrock_smoke, "reset_model_state", lambda **kwargs: None)
    catalog = [f"route-{index}" for index in range(40)]
    _install_catalog(monkeypatch, runtime=catalog, catalog=catalog)
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
    catalog = [f"route-{index}" for index in range(80)]
    _install_catalog(monkeypatch, runtime=catalog, catalog=catalog)
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
    assert captured == {
        "models": [],
        "max_attempts": bedrock_smoke.DEFAULT_SMOKE_ROUTE_LIMIT,
    }
