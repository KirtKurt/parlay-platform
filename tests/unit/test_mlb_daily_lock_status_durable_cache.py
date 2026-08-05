from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
    return dict(event.get("queryStringParameters") or {})


def _module(table):
    calls = {"status": 0, "delegated": 0}

    def status_payload(slate_date=None):
        calls["status"] += 1
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": "test-model",
            "slateDateEt": slate_date or "2026-08-05",
            "gameCount": 15,
            "perGameStatus": [],
        }

    def handle(_event, _context):
        calls["delegated"] += 1
        return _response(200, {"delegated": True})

    module = SimpleNamespace(
        TABLE=table,
        MODEL_VERSION="test-model",
        _today_et=lambda: "2026-08-05",
        _payload=_payload,
        _resp=_response,
        _status_payload=status_payload,
        handle=handle,
    )
    fake_diagnostic_module = SimpleNamespace(
        ATTEMPT_DIAGNOSTICS_VERSION="test",
        _diagnostic_history=lambda *_args: {},
        game_identity=lambda game: game.get("gameIdentity"),
    )
    original = route_patch.import_module
    route_patch.import_module = lambda _name: fake_diagnostic_module
    try:
        route_patch.apply(module)
    finally:
        route_patch.import_module = original
    return module, calls


def test_first_summary_request_persists_and_second_request_hits_cache(monkeypatch):
    monkeypatch.setenv("MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS", "1200")
    table = Table()
    module, calls = _module(table)
    event = {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": None,
    }

    first = module.handle(event, None)
    second = module.handle(event, None)
    first_body = json.loads(first["body"])
    second_body = json.loads(second["body"])

    assert first["statusCode"] == 200
    assert second["statusCode"] == 200
    assert calls["status"] == 1
    assert table.put_calls == 1
    assert table.get_calls == 2
    assert first_body["statusCache"]["hit"] is False
    assert second_body["statusCache"]["hit"] is True
    assert second_body["readOnly"] is True
    assert second_body["statusDetail"] == "SUMMARY"


def test_full_diagnostics_bypasses_summary_cache(monkeypatch):
    monkeypatch.setenv("MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS", "1200")
    table = Table()
    module, calls = _module(table)
    event = {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"includeAttemptDiagnostics": "true"},
    }

    first = module.handle(event, None)
    second = module.handle(event, None)

    assert first["statusCode"] == 200
    assert second["statusCode"] == 200
    assert calls["status"] == 2
    assert table.put_calls == 0


def test_stale_summary_cache_recomputes(monkeypatch):
    monkeypatch.setenv("MLB_LOCK_STATUS_CACHE_MAX_AGE_SECONDS", "30")
    table = Table()
    module, calls = _module(table)
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)
    table.items[("MLB_LOCK_STATUS_CACHE#2026-08-05", route_patch.CACHE_SK)] = {
        "PK": "MLB_LOCK_STATUS_CACHE#2026-08-05",
        "SK": route_patch.CACHE_SK,
        "record_type": route_patch.CACHE_RECORD_TYPE,
        "updated_at": stale.isoformat(),
        "data_json": json.dumps(
            {
                "ok": True,
                "sport": "mlb",
                "modelVersion": "test-model",
                "slateDateEt": "2026-08-05",
            }
        ),
    }
    event = {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": None,
    }

    response = module.handle(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert calls["status"] == 1
    assert table.put_calls == 1
    assert body["statusCache"]["hit"] is False
