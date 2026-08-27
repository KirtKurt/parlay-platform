from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_daily_lock_status_route_patch as route_patch


class Table:
    def __init__(self):
        self.items = {}
        self.get_calls = 0
        self.put_calls = 0

    def get_item(self, *, Key, ConsistentRead):
        assert ConsistentRead is True
        self.get_calls += 1
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def put_item(self, *, Item):
        self.put_calls += 1
        self.items[(Item["PK"], Item["SK"])] = Item
        return {}


def _response(status, body):
    return {"statusCode": status, "body": json.dumps(body)}


def _payload(event):
    payload = dict(event.get("queryStringParameters") or {})
    body = event.get("body")
    if isinstance(body, str) and body:
        payload.update(json.loads(body))
    return payload


def _module(table):
    calls = {"status": 0, "delegated": 0}

    def status_payload(slate_date=None):
        calls["status"] += 1
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": "test-model",
            "slateDateEt": slate_date or "2026-08-26",
            "gameCount": 15,
            "perGameStatus": [],
        }

    def handle(_event, _context):
        calls["delegated"] += 1
        return _response(200, {"delegated": True})

    module = SimpleNamespace(
        TABLE=table,
        MODEL_VERSION="test-model",
        _today_et=lambda: "2026-08-26",
        _payload=_payload,
        _resp=_response,
        _status_payload=status_payload,
        handle=handle,
    )
    diagnostics = SimpleNamespace(
        ATTEMPT_DIAGNOSTICS_VERSION="test",
        _diagnostic_history=lambda *_args, **_kwargs: {},
        game_identity=lambda game: game.get("gameIdentity"),
    )
    original_import = route_patch.import_module
    route_patch.import_module = lambda _name: diagnostics
    try:
        route_patch.apply(module)
    finally:
        route_patch.import_module = original_import
    return module, calls


def test_http_api_v2_status_request_populates_then_hits_durable_cache(monkeypatch):
    monkeypatch.setenv("MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS", "1200")
    table = Table()
    module, calls = _module(table)
    event = {
        "version": "2.0",
        "routeKey": "GET /v1/mlb/locks/status",
        "rawPath": "/v1/mlb/locks/status",
        "requestContext": {
            "http": {
                "method": "GET",
                "path": "/v1/mlb/locks/status",
            }
        },
        "queryStringParameters": None,
    }

    first = module.handle(event, None)
    second = module.handle(event, None)
    first_body = json.loads(first["body"])
    second_body = json.loads(second["body"])

    assert first["statusCode"] == 200
    assert second["statusCode"] == 200
    assert calls["status"] == 1
    assert calls["delegated"] == 0
    assert table.put_calls == 1
    assert table.get_calls == 2
    assert first_body["statusCache"]["hit"] is False
    assert second_body["statusCache"]["hit"] is True
    assert second_body["statusCache"]["version"] == route_patch.CACHE_VERSION


def test_lock_lambda_explicitly_installs_cache_after_runtime_patches():
    source = (HELLO_WORLD / "mlb_daily_pick_lock.py").read_text(encoding="utf-8")
    ensure_index = source.index("def _ensure_durable_status_route()")
    handler_index = source.index("def lambda_handler(event, context):")

    assert ensure_index < handler_index
    assert "mlb_daily_lock_status_route_patch.apply(sys.modules[__name__])" in source
    handler_body = source[handler_index:]
    assert "_ensure_durable_status_route()" in handler_body
    assert "return handle(event, context)" in handler_body


def test_cache_contract_is_explicit_v2():
    assert route_patch.CACHE_VERSION == (
        "MLB-LOCK-STATUS-CACHE-v2-explicit-http-api-v2"
    )
