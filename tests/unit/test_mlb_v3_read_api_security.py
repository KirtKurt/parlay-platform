from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]


def _load_read_api(monkeypatch, calls, *, runtime_ok=True):
    runtime = ModuleType("mlb_ml_runtime_install_v3")
    runtime.install = lambda: {"ok": runtime_ok, "steps": {}, "version": "test-runtime"}

    engine = ModuleType("mlb_game_winner_engine")
    engine.MODEL_VERSION = "test-model"
    engine.ENGINE = "test-engine"
    engine.MLB_ML_RUNTIME_INSTALL_V3 = runtime.install()

    def predict_all(date, *, store, limit):
        calls.append({"date": date, "store": store, "limit": limit})
        return {"ok": True, "predictions": [{"gameId": "game-1"}], "count": 1}

    engine.predict_all = predict_all

    optimization = ModuleType("mlb_ml_optimization_v3")
    optimization.VERSION = "test-optimization"
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    monkeypatch.setitem(sys.modules, engine.__name__, engine)
    monkeypatch.setitem(sys.modules, optimization.__name__, optimization)

    module_name = "test_mlb_v3_read_api_module"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "hello_world" / "mlb_v3_read_api.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resource_block(template: str, resource_name: str) -> str:
    lines = template.splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"  {resource_name}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("  ") and not lines[i].startswith("    "):
            end = i
            break
    return "\n".join(lines[start:end])


def test_public_read_api_ignores_store_query_parameter(monkeypatch):
    calls = []
    api = _load_read_api(monkeypatch, calls)

    response = api.lambda_handler(
        {
            "rawPath": "/v1/mlb/predictions",
            "httpMethod": "GET",
            "queryStringParameters": {
                "date": "2026-07-16",
                "store": "true",
                "limit": "17",
            },
        },
        None,
    )

    assert response["statusCode"] == 200
    assert calls == [{"date": "2026-07-16", "store": False, "limit": 17}]
    body = json.loads(response["body"])
    assert body["readOnly"] is True
    assert body["apiRuntimeVersion"] == "MLB-V3-READ-API-v3-read-only"


def test_public_read_api_fails_closed_when_runtime_install_is_not_ok(monkeypatch):
    calls = []
    api = _load_read_api(monkeypatch, calls, runtime_ok=False)

    response = api.lambda_handler(
        {
            "rawPath": "/v1/mlb/game-winners",
            "httpMethod": "GET",
            "queryStringParameters": {"date": "2026-07-16", "store": "true"},
        },
        None,
    )

    assert response["statusCode"] == 503
    assert calls == []
    body = json.loads(response["body"])
    assert body["ok"] is False
    assert body["predictions"] == []
    assert body["winner_predictions"] == []
    assert body["count"] == 0

    model_response = api.lambda_handler(
        {"rawPath": "/v1/mlb/model/version", "httpMethod": "GET"},
        None,
    )
    assert model_response["statusCode"] == 503


def test_legacy_public_mlb_surfaces_are_also_read_only_and_fail_closed(monkeypatch):
    hello = ROOT / "hello_world"
    if str(hello) not in sys.path:
        sys.path.insert(0, str(hello))
    import inqsi_mlb_v1_core as core

    calls = []
    engine = ModuleType("legacy_public_test_engine")
    engine.MLB_ML_RUNTIME_INSTALL_V3 = {"ok": True}
    engine.predict_all = lambda date, *, store, limit: calls.append({
        "date": date,
        "store": store,
        "limit": limit,
    }) or {"ok": True, "predictions": [], "count": 0}
    monkeypatch.setattr(core, "_engine", lambda: engine)

    payload = core.predictions("2026-07-16", 9, store=True)
    assert payload["ok"] is True
    assert payload["readOnly"] is True
    assert payload["storage"]["callerRequestedWriteIgnored"] is True
    assert calls == [{"date": "2026-07-16", "store": False, "limit": 9}]

    engine.MLB_ML_RUNTIME_INSTALL_V3 = {"ok": False}
    failed = core.handle({
        "path": "/v1/mlb/predictions",
        "httpMethod": "GET",
        "queryStringParameters": {"date": "2026-07-16", "store": "true"},
    }, None)
    assert failed["statusCode"] == 503
    assert calls == [{"date": "2026-07-16", "store": False, "limit": 9}]

    startup_source = (ROOT / "hello_world" / "usercustomize.py").read_text()
    assert 'payload = engine.predict_all(\n                    date,\n                    store=False,' in startup_source
    assert "MLB_PUBLIC_READ_RUNTIME_NOT_READY" in startup_source


def test_security_template_gives_public_read_lambda_no_crud_policy(tmp_path):
    shutil.copy2(ROOT / "template.yaml", tmp_path / "template.yaml")
    v1_patcher = ROOT / "scripts" / "patch_template_mlb_v1.py"
    patcher = ROOT / "scripts" / "patch_template_mlb_security.py"

    subprocess.run([sys.executable, str(v1_patcher)], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run([sys.executable, str(patcher)], cwd=tmp_path, check=True, capture_output=True, text=True)
    template = (tmp_path / "template.yaml").read_text()
    block = _resource_block(template, "MLBV3ReadFunction")
    assert "DynamoDBReadPolicy:" in block
    assert "DynamoDBCrudPolicy:" not in block

    # The patch also repairs an already-installed legacy CRUD grant, while
    # leaving authenticated writer resources unchanged.
    legacy_block = block.replace("DynamoDBReadPolicy:", "DynamoDBCrudPolicy:")
    assert legacy_block != block
    (tmp_path / "template.yaml").write_text(template.replace(block, legacy_block, 1))
    subprocess.run([sys.executable, str(patcher)], cwd=tmp_path, check=True, capture_output=True, text=True)
    repaired = _resource_block((tmp_path / "template.yaml").read_text(), "MLBV3ReadFunction")
    assert "DynamoDBReadPolicy:" in repaired
    assert "DynamoDBCrudPolicy:" not in repaired
