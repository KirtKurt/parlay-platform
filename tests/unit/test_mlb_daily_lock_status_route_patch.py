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


SLATE = "2026-08-02"


def _request_payload(event):
    payload = dict(event.get("queryStringParameters") or {})
    body = event.get("body")
    if body:
        payload.update(json.loads(body))
    return payload


def _response(status, body):
    return {"statusCode": status, "body": json.dumps(body)}


def _build_module():
    calls = {"delegated": [], "diagnostics": 0, "status": []}

    def full_diagnostics(_module, slate_date, game):
        calls["diagnostics"] += 1
        return {
            "ok": True,
            "version": "test-diagnostics-v1",
            "slateDateEt": slate_date,
            "gameIdentity": game["game_id"],
            "attemptCount": 2,
            "attempts": [{"attemptId": "a"}, {"attemptId": "b"}],
        }

    fake_diagnostic_module = SimpleNamespace(
        ATTEMPT_DIAGNOSTICS_VERSION="test-diagnostics-v1",
        game_identity=lambda game: game["game_id"],
        _diagnostic_history=full_diagnostics,
    )

    module = SimpleNamespace()
    module.MODEL_VERSION = "test-lock-model"
    module._payload = _request_payload
    module._resp = _response

    def status_payload(slate_date=None):
        slate = slate_date or SLATE
        calls["status"].append(slate)
        return {
            "ok": True,
            "slateDateEt": slate,
            "gameCount": 1,
            "perGameStatus": [
                {
                    "gameIdentity": "g1",
                    "attemptDiagnostics": fake_diagnostic_module._diagnostic_history(
                        module,
                        slate,
                        {"game_id": "g1"},
                    ),
                }
            ],
        }

    def delegated_handle(event, _context):
        payload = module._payload(event)
        calls["delegated"].append(payload)
        return module._resp(200, {"delegated": True, "payload": payload})

    module._status_payload = status_payload
    module.handle = delegated_handle

    # Keep this unit test hermetic. Importing and wrapping the production
    # per-game module here would leak a captured function across the full
    # pytest process and bypass later test-local module construction.
    original_import_module = route_patch.import_module
    route_patch.import_module = lambda _name: fake_diagnostic_module
    try:
        route_patch.apply(module)
    finally:
        route_patch.import_module = original_import_module
    return module, calls


def _assert_summary_status(response, calls):
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert calls["delegated"] == []
    assert calls["status"] == [SLATE]
    assert calls["diagnostics"] == 0
    assert body["readOnly"] is True
    assert body["statusDetail"] == "SUMMARY"
    assert body["attemptDiagnosticsIncluded"] is False
    diagnostics = body["perGameStatus"][0]["attemptDiagnostics"]
    assert diagnostics["omitted"] is True
    assert diagnostics["omissionReason"] == "READ_ONLY_STATUS_SUMMARY"
    assert diagnostics["attemptCount"] is None


def test_deployed_plural_lock_status_is_read_only_and_bounded():
    module, calls = _build_module()

    response = module.handle(
        {
            "httpMethod": "GET",
            "path": "/v1/mlb/locks/status",
            "queryStringParameters": None,
        },
        None,
    )

    _assert_summary_status(response, calls)


def test_legacy_post_lock_status_is_read_only_and_normalizes_current_date():
    module, calls = _build_module()

    response = module.handle(
        {
            "httpMethod": "POST",
            "path": "/api/mlb/lock-status",
            "body": json.dumps(
                {
                    "allowInference": False,
                    "mode": "today",
                    "date": "current",
                }
            ),
        },
        None,
    )

    _assert_summary_status(response, calls)


def test_lock_status_can_explicitly_request_full_attempt_diagnostics():
    module, calls = _build_module()

    response = module.handle(
        {
            "httpMethod": "GET",
            "path": "/v1/mlb/locks/status",
            "queryStringParameters": {
                "date": SLATE,
                "includeAttemptDiagnostics": "true",
            },
        },
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert calls["delegated"] == []
    assert calls["diagnostics"] == 1
    assert body["statusDetail"] == "FULL"
    assert body["attemptDiagnosticsIncluded"] is True
    diagnostics = body["perGameStatus"][0]["attemptDiagnostics"]
    assert diagnostics["attemptCount"] == 2
    assert diagnostics.get("omitted") is not True


def test_date_aliases_are_normalized_before_non_status_routes_delegate():
    module, calls = _build_module()

    response = module.handle(
        {
            "httpMethod": "POST",
            "path": "/api/mlb/lock",
            "body": json.dumps({"date": "today", "force": False}),
        },
        None,
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["delegated"] is True
    assert body["payload"] == {"force": False}
    assert calls["delegated"] == [{"force": False}]


def test_explicit_historical_date_is_preserved():
    module, _calls = _build_module()

    response = module.handle(
        {
            "httpMethod": "POST",
            "path": "/api/mlb/lock",
            "body": json.dumps({"date": "2026-07-31"}),
        },
        None,
    )
    body = json.loads(response["body"])

    assert body["payload"]["date"] == "2026-07-31"
