from __future__ import annotations

"""Apply an idempotent MLB-only deployment-smoke timeout repair."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHANGED: list[str] = []


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    old = target.read_text(encoding="utf-8") if target.exists() else None
    if old == text:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    CHANGED.append(path)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text and old not in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}:EXPECTED_ONE_MARKER:FOUND_{count}")
    return text.replace(old, new, 1)


def patch_gateway() -> None:
    path = "mlb_auto_llm/production_model_gateway.py"
    text = read(path)
    old_signature = '''def invoke_chain_text(
    prompt: str,
    models: Optional[Iterable[str]] = None,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
) -> Dict[str, Any]:
'''
    new_signature = '''def invoke_chain_text(
    prompt: str,
    models: Optional[Iterable[str]] = None,
    *,
    client: Any = None,
    max_tokens: int = 900,
    temperature: float = 0.0,
    top_p: float = 0.9,
    max_attempts: Optional[int] = None,
) -> Dict[str, Any]:
'''
    text = replace_once(
        text,
        old_signature,
        new_signature,
        "invoke_chain_text_signature",
    )
    text = replace_once(
        text,
        "    catalog = _ordered_routes(models or configured_models())\n",
        "    catalog = _ordered_routes(\n"
        "        configured_models() if models is None else models\n"
        "    )\n",
        "explicit_empty_catalog",
    )
    old_attempts = '''    max_attempts = max(
        1, int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8"))
    )
'''
    new_attempts = '''    configured_max_attempts = (
        max_attempts
        if max_attempts is not None
        else int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8"))
    )
    max_attempts = max(1, int(configured_max_attempts))
'''
    text = replace_once(
        text,
        old_attempts,
        new_attempts,
        "explicit_attempt_bound",
    )
    write(path, text)


def patch_smoke() -> None:
    path = "mlb_auto_llm/bedrock_smoke.py"
    text = read(path)
    if "import os\n" not in text:
        text = replace_once(
            text,
            "from __future__ import annotations\n\n",
            "from __future__ import annotations\n\nimport os\n",
            "smoke_os_import",
        )
    old = '''    reset_model_state(clear_discovery=True, clear_failures=False)
    mantle = mantle_models()
    runtime = runtime_models()
    models = configured_models()
    result = invoke_chain_text(
        "Return only the word OK.",
        models,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
    )
'''
    new = '''    reset_model_state(clear_discovery=True, clear_failures=False)
    # This deployment probe is deliberately bounded. Production predictions
    # continue to use configured_models() and the complete failover catalog.
    mantle = []
    runtime = runtime_models()
    smoke_limit = max(
        1,
        min(
            int(os.environ.get("MLB_AUTO_BEDROCK_SMOKE_ROUTE_LIMIT", "1")),
            3,
        ),
    )
    models = runtime[:smoke_limit]
    result = invoke_chain_text(
        "Return only the word OK.",
        models,
        max_tokens=8,
        temperature=0.0,
        top_p=0.9,
        max_attempts=smoke_limit,
    )
'''
    text = replace_once(text, old, new, "bounded_bedrock_smoke")
    write(path, text)


def patch_deploy_workflow() -> None:
    path = ".github/workflows/deploy-mlb-auto-llm.yml"
    text = read(path)
    slash = chr(92)
    compile_anchor = f"            mlb_auto_llm/model_gateway.py {slash}\n"
    compile_line = f"            mlb_auto_llm/production_model_gateway.py {slash}\n"
    if "mlb_auto_llm/production_model_gateway.py" not in text:
        if compile_anchor not in text:
            raise RuntimeError("DEPLOY_COMPILE_ANCHOR_MISSING")
        text = text.replace(compile_anchor, compile_anchor + compile_line, 1)

    test_anchor = (
        f"            tests/unit/test_mlb_auto_model_gateway.py {slash}\n"
        "            tests/unit/test_mlb_auto_ml_authority.py\n"
    )
    test_block = (
        f"            tests/unit/test_mlb_auto_model_gateway.py {slash}\n"
        f"            tests/unit/test_mlb_auto_production_model_gateway.py {slash}\n"
        f"            tests/unit/test_mlb_auto_bedrock_smoke.py {slash}\n"
        f"            tests/unit/test_mlb_auto_ml_authority_smoke.py {slash}\n"
        "            tests/unit/test_mlb_auto_ml_authority.py\n"
    )
    if "tests/unit/test_mlb_auto_bedrock_smoke.py" not in text:
        if test_anchor not in text:
            raise RuntimeError("DEPLOY_TEST_ANCHOR_MISSING")
        text = text.replace(test_anchor, test_block, 1)
    write(path, text)


def patch_tests() -> None:
    path = "tests/unit/test_mlb_auto_production_model_gateway.py"
    text = read(path)
    if "def test_explicit_max_attempts_override_is_bounded" not in text:
        text += '''


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
'''
    write(path, text)

    write(
        "tests/unit/test_mlb_auto_bedrock_smoke.py",
        '''from __future__ import annotations

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
''',
    )

    write(
        "tests/unit/test_mlb_auto_ml_authority_smoke.py",
        '''from __future__ import annotations

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
''',
    )


def verify_scope() -> None:
    forbidden = ("tennis", "soccer", "nfl")
    for path in CHANGED:
        if any(token in path.lower() for token in forbidden):
            raise RuntimeError(f"SPORT_ISOLATION_VIOLATION:{path}")


def main() -> None:
    patch_gateway()
    patch_smoke()
    patch_deploy_workflow()
    patch_tests()
    verify_scope()
    print("MLB bounded deployment smoke repair V3 applied")
    for path in CHANGED:
        print(f"changed={path}")


if __name__ == "__main__":
    main()
