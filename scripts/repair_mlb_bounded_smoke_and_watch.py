from __future__ import annotations

"""Apply the MLB-only bounded deployment-smoke and runtime-watch repair.

This script is intentionally idempotent. It changes no prediction thresholds,
no settled outcomes, and no Tennis, Soccer, or NFL files.
"""

from pathlib import Path
from textwrap import dedent, indent
from typing import List


ROOT = Path(__file__).resolve().parents[1]
CHANGED: List[str] = []


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"REQUIRED_FILE_MISSING:{relative}")
    return path.read_text(encoding="utf-8")


def _write(relative: str, content: str) -> None:
    path = ROOT / relative
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    CHANGED.append(relative)


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text and old not in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}:EXPECTED_ONE_MARKER:FOUND_{count}")
    return text.replace(old, new, 1)


def patch_production_gateway() -> None:
    relative = "mlb_auto_llm/production_model_gateway.py"
    text = _read(relative)
    text = _replace_once(
        text,
        dedent(
            """\
                top_p: float = 0.9,
            ) -> Dict[str, Any]:
            """
        ),
        dedent(
            """\
                top_p: float = 0.9,
                max_attempts: Optional[int] = None,
            ) -> Dict[str, Any]:
            """
        ),
        "gateway_explicit_max_attempts_parameter",
    )
    text = _replace_once(
        text,
        "    catalog = _ordered_routes(models or configured_models())\n",
        "    catalog = _ordered_routes(\n"
        "        configured_models() if models is None else models\n"
        "    )\n",
        "gateway_explicit_empty_catalog_semantics",
    )
    text = _replace_once(
        text,
        dedent(
            """\
                max_attempts = max(
                    1, int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8"))
                )
            """
        ),
        dedent(
            """\
                configured_max_attempts = (
                    max_attempts
                    if max_attempts is not None
                    else int(os.environ.get("MLB_AUTO_BEDROCK_MAX_MODEL_ATTEMPTS", "8"))
                )
                max_attempts = max(1, int(configured_max_attempts))
            """
        ),
        "gateway_bounded_attempt_resolution",
    )
    _write(relative, text)


def patch_bedrock_smoke() -> None:
    relative = "mlb_auto_llm/bedrock_smoke.py"
    text = _read(relative)
    if "import os\n" not in text:
        text = _replace_once(
            text,
            "from __future__ import annotations\n\n",
            "from __future__ import annotations\n\nimport os\n",
            "bedrock_smoke_os_import",
        )
    text = _replace_once(
        text,
        dedent(
            """\
                reset_model_state(clear_discovery=True, clear_failures=False)
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
            """
        ),
        dedent(
            """\
                reset_model_state(clear_discovery=True, clear_failures=False)
                # Deployment smoke is bounded. Real prediction inference still
                # uses configured_models() and its complete failover catalog.
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
            """
        ),
        "bounded_runtime_only_bedrock_smoke",
    )
    _write(relative, text)


def patch_deploy_workflow() -> None:
    relative = ".github/workflows/deploy-mlb-auto-llm.yml"
    text = _read(relative)
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
    test_replacement = (
        f"            tests/unit/test_mlb_auto_model_gateway.py {slash}\n"
        f"            tests/unit/test_mlb_auto_production_model_gateway.py {slash}\n"
        f"            tests/unit/test_mlb_auto_bedrock_smoke.py {slash}\n"
        f"            tests/unit/test_mlb_auto_ml_authority_smoke.py {slash}\n"
        "            tests/unit/test_mlb_auto_ml_authority.py\n"
    )
    if "tests/unit/test_mlb_auto_bedrock_smoke.py" not in text:
        if test_anchor not in text:
            raise RuntimeError("DEPLOY_TEST_ANCHOR_MISSING")
        text = text.replace(test_anchor, test_replacement, 1)
    _write(relative, text)


def patch_tests() -> None:
    relative = "tests/unit/test_mlb_auto_production_model_gateway.py"
    text = _read(relative)
    marker = "def test_explicit_max_attempts_override_is_bounded"
    if marker not in text:
        text += dedent(
            """

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


            def test_explicit_empty_catalog_does_not_expand_to_full_catalog(
                monkeypatch,
            ) -> None:
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
            """
        )
    _write(relative, text)

    _write(
        "tests/unit/test_mlb_auto_bedrock_smoke.py",
        dedent(
            """\
            from __future__ import annotations

            from mlb_auto_llm import bedrock_smoke


            def test_deployment_smoke_uses_one_runtime_route_only(monkeypatch) -> None:
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
                    captured["prompt"] = prompt
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


            def test_deployment_smoke_empty_runtime_catalog_fails_fast(monkeypatch) -> None:
                monkeypatch.setattr(
                    bedrock_smoke, "reset_model_state", lambda **kwargs: None
                )
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
            """
        ),
    )

    _write(
        "tests/unit/test_mlb_auto_ml_authority_smoke.py",
        dedent(
            """\
            from __future__ import annotations

            from mlb_auto_llm import ml_authority_smoke


            def test_deployment_smoke_prefers_bedrock_success(monkeypatch) -> None:
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


            def test_deployment_smoke_uses_ml_after_bounded_bedrock_failure(
                monkeypatch,
            ) -> None:
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
            """
        ),
    )


def patch_runtime_watch() -> None:
    relative = ".github/workflows/mlb-three-source-runtime-watch-v3.yml"
    text = _read(relative)
    old = indent(
        dedent(
            """\
            def resource(logical_id: str) -> str:
                return cfn.describe_stack_resource(
                    StackName=STACK,
                    LogicalResourceId=logical_id,
                )['StackResourceDetail']['PhysicalResourceId']

            controller_name = resource('MLBThreeApiAutonomousControllerFunction')
            config = lam.get_function_configuration(FunctionName=controller_name)
            """
        ),
        "          ",
    )
    new = indent(
        dedent(
            """\
            def stack_lambda_resources():
                rows = []
                token = None
                while True:
                    kwargs = {"StackName": STACK}
                    if token:
                        kwargs["NextToken"] = token
                    page = cfn.list_stack_resources(**kwargs)
                    rows.extend(
                        row
                        for row in page.get("StackResourceSummaries") or []
                        if row.get("ResourceType") == "AWS::Lambda::Function"
                        and row.get("PhysicalResourceId")
                    )
                    token = page.get("NextToken")
                    if not token:
                        return rows

            required_env = (
                'MLB_THREE_API_STATE_TABLE',
                'MLB_THREE_API_PULL_FUNCTION_NAME',
                'MLB_THREE_API_LOCK_FUNCTION_NAME',
                'MLB_THREE_API_TRAIN_FUNCTION_NAME',
                'MLB_THREE_API_VERIFY_FUNCTION_NAME',
            )
            candidates = []
            for resource_row in stack_lambda_resources():
                candidate_name = resource_row['PhysicalResourceId']
                candidate_config = lam.get_function_configuration(
                    FunctionName=candidate_name
                )
                candidate_env = (
                    (candidate_config.get('Environment') or {}).get('Variables') or {}
                )
                handler = str(candidate_config.get('Handler') or '')
                if (
                    handler.startswith('mlb_three_api_autonomous_controller_')
                    and handler.endswith('.lambda_handler')
                    and all(bool(candidate_env.get(key)) for key in required_env)
                ):
                    candidates.append((candidate_name, candidate_config))
            if len(candidates) != 1:
                raise RuntimeError(
                    'MLB_THREE_API_CONTROLLER_DISCOVERY_FAILED:'
                    + json.dumps(
                        {
                            'candidateCount': len(candidates),
                            'candidateNames': [row[0] for row in candidates],
                        },
                        sort_keys=True,
                    )
                )
            controller_name, config = candidates[0]
            """
        ),
        "          ",
    )
    text = _replace_once(text, old, new, "runtime_watch_controller_discovery")
    text = text.replace(
        "'currentControllerLogicalIdResolved': True,",
        "'currentControllerDiscovered': len(candidates) == 1,",
        1,
    )
    _write(relative, text)


def verify_scope() -> None:
    forbidden = ("tennis", "soccer", "nfl")
    for relative in CHANGED:
        lowered = relative.lower()
        if any(token in lowered for token in forbidden):
            raise RuntimeError(f"SPORT_ISOLATION_VIOLATION:{relative}")


def main() -> None:
    patch_production_gateway()
    patch_bedrock_smoke()
    patch_deploy_workflow()
    patch_tests()
    patch_runtime_watch()
    verify_scope()
    print("MLB bounded smoke/runtime-watch repair applied")
    for relative in CHANGED:
        print(f"changed={relative}")


if __name__ == "__main__":
    main()
