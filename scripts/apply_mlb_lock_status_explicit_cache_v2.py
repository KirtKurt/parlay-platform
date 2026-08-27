from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROUTE = ROOT / "hello_world" / "mlb_daily_lock_status_route_patch.py"
LOCK = ROOT / "hello_world" / "mlb_daily_pick_lock.py"
TEST = ROOT / "tests" / "unit" / "test_mlb_lock_status_explicit_cache_install.py"
WORKFLOW = ROOT / ".github" / "workflows" / "apply-mlb-lock-status-explicit-cache-v2-once.yml"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_route() -> None:
    text = ROUTE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'CACHE_VERSION = "MLB-LOCK-STATUS-CACHE-v1-durable-summary"',
        'CACHE_VERSION = "MLB-LOCK-STATUS-CACHE-v2-explicit-http-api-v2"',
        label="cache version",
    )
    anchor = '''def _lock_status_path(path: Any) -> bool:\n    normalized = "/" + str(path or "").strip().strip("/")\n    return any(normalized.endswith(suffix) for suffix in _LOCK_STATUS_SUFFIXES)\n\n\n'''
    event_method = '''def _event_method(event: Dict[str, Any]) -> str:\n    event = event or {}\n    request_context = event.get("requestContext") or {}\n    http_context = request_context.get("http") or {}\n    return str(\n        event.get("httpMethod")\n        or http_context.get("method")\n        or request_context.get("httpMethod")\n        or ""\n    ).strip().upper()\n\n\n'''
    if "def _event_method(" not in text:
        text = replace_once(
            text,
            anchor,
            anchor + event_method,
            label="event method insertion",
        )
    text = replace_once(
        text,
        '        method = str(event.get("httpMethod") or "").upper()',
        '        method = _event_method(event)',
        label="route method extraction",
    )
    ROUTE.write_text(text, encoding="utf-8")


def patch_lock_entrypoint() -> None:
    text = LOCK.read_text(encoding="utf-8")
    old = '''def lambda_handler(event, context):\n    return handle(event, context)\n'''
    new = '''def _ensure_durable_status_route() -> None:\n    # Site customization can import this module before usercustomize runs.\n    # Install the public status cache at invocation time, after every earlier\n    # lock patch has finished, so the route wrapper cannot be lost to import\n    # ordering or a disabled user-site customization hook.\n    import sys\n\n    import mlb_daily_lock_status_route_patch\n\n    mlb_daily_lock_status_route_patch.apply(sys.modules[__name__])\n\n\ndef lambda_handler(event, context):\n    _ensure_durable_status_route()\n    return handle(event, context)\n'''
    text = replace_once(text, old, new, label="lock lambda entrypoint")
    LOCK.write_text(text, encoding="utf-8")


def write_test() -> None:
    TEST.write_text(
        '''from __future__ import annotations\n\nimport json\nimport sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\n\n\nROOT = Path(__file__).resolve().parents[2]\nHELLO_WORLD = ROOT / "hello_world"\nif str(HELLO_WORLD) not in sys.path:\n    sys.path.insert(0, str(HELLO_WORLD))\n\nimport mlb_daily_lock_status_route_patch as route_patch\n\n\nclass Table:\n    def __init__(self):\n        self.items = {}\n        self.get_calls = 0\n        self.put_calls = 0\n\n    def get_item(self, *, Key, ConsistentRead):\n        assert ConsistentRead is True\n        self.get_calls += 1\n        item = self.items.get((Key["PK"], Key["SK"]))\n        return {"Item": item} if item else {}\n\n    def put_item(self, *, Item):\n        self.put_calls += 1\n        self.items[(Item["PK"], Item["SK"])] = Item\n        return {}\n\n\ndef _response(status, body):\n    return {"statusCode": status, "body": json.dumps(body)}\n\n\ndef _payload(event):\n    payload = dict(event.get("queryStringParameters") or {})\n    body = event.get("body")\n    if isinstance(body, str) and body:\n        payload.update(json.loads(body))\n    return payload\n\n\ndef _module(table):\n    calls = {"status": 0, "delegated": 0}\n\n    def status_payload(slate_date=None):\n        calls["status"] += 1\n        return {\n            "ok": True,\n            "sport": "mlb",\n            "modelVersion": "test-model",\n            "slateDateEt": slate_date or "2026-08-26",\n            "gameCount": 15,\n            "perGameStatus": [],\n        }\n\n    def handle(_event, _context):\n        calls["delegated"] += 1\n        return _response(200, {"delegated": True})\n\n    module = SimpleNamespace(\n        TABLE=table,\n        MODEL_VERSION="test-model",\n        _today_et=lambda: "2026-08-26",\n        _payload=_payload,\n        _resp=_response,\n        _status_payload=status_payload,\n        handle=handle,\n    )\n    diagnostics = SimpleNamespace(\n        ATTEMPT_DIAGNOSTICS_VERSION="test",\n        _diagnostic_history=lambda *_args, **_kwargs: {},\n        game_identity=lambda game: game.get("gameIdentity"),\n    )\n    original_import = route_patch.import_module\n    route_patch.import_module = lambda _name: diagnostics\n    try:\n        route_patch.apply(module)\n    finally:\n        route_patch.import_module = original_import\n    return module, calls\n\n\ndef test_http_api_v2_status_request_populates_then_hits_durable_cache(monkeypatch):\n    monkeypatch.setenv("MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS", "1200")\n    table = Table()\n    module, calls = _module(table)\n    event = {\n        "version": "2.0",\n        "routeKey": "GET /v1/mlb/locks/status",\n        "rawPath": "/v1/mlb/locks/status",\n        "requestContext": {\n            "http": {\n                "method": "GET",\n                "path": "/v1/mlb/locks/status",\n            }\n        },\n        "queryStringParameters": None,\n    }\n\n    first = module.handle(event, None)\n    second = module.handle(event, None)\n    first_body = json.loads(first["body"])\n    second_body = json.loads(second["body"])\n\n    assert first["statusCode"] == 200\n    assert second["statusCode"] == 200\n    assert calls["status"] == 1\n    assert calls["delegated"] == 0\n    assert table.put_calls == 1\n    assert table.get_calls == 2\n    assert first_body["statusCache"]["hit"] is False\n    assert second_body["statusCache"]["hit"] is True\n    assert second_body["statusCache"]["version"] == route_patch.CACHE_VERSION\n\n\ndef test_lock_lambda_explicitly_installs_cache_after_runtime_patches():\n    source = (HELLO_WORLD / "mlb_daily_pick_lock.py").read_text(encoding="utf-8")\n    ensure_index = source.index("def _ensure_durable_status_route()")\n    handler_index = source.index("def lambda_handler(event, context):")\n\n    assert ensure_index < handler_index\n    assert "mlb_daily_lock_status_route_patch.apply(sys.modules[__name__])" in source\n    handler_body = source[handler_index:]\n    assert "_ensure_durable_status_route()" in handler_body\n    assert "return handle(event, context)" in handler_body\n\n\ndef test_cache_contract_is_explicit_v2():\n    assert route_patch.CACHE_VERSION == (\n        "MLB-LOCK-STATUS-CACHE-v2-explicit-http-api-v2"\n    )\n''',
        encoding="utf-8",
    )


def remove_scaffold() -> None:
    for path in (Path(__file__), WORKFLOW):
        if path.exists():
            path.unlink()


def main() -> None:
    patch_route()
    patch_lock_entrypoint()
    write_test()
    remove_scaffold()


if __name__ == "__main__":
    main()
