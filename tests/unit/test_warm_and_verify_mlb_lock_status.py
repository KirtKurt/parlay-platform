from __future__ import annotations

import base64
import io
import json

import pytest

from scripts import warm_and_verify_mlb_lock_status as subject


def _payload():
    return {
        "ok": True,
        "sport": "mlb",
        "readOnly": True,
        "slateDateEt": "2026-08-04",
        "modelVersion": subject.EXPECTED_MODEL_VERSION,
        "lockPolicy": "each_mlb_game_minus_45_minutes",
        "lockMinutesBeforeEachGame": 45,
        "readinessCheckpointsMinutesBeforeGame": [60, 50],
        "playabilityCheckpointsMinutesBeforeGame": [30, 15],
        "gameCount": 1,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": subject.EXPECTED_SCHEDULE_VERSION,
        "officialScheduleGameCount": 1,
        "officialScheduleAuthoritativeStartTimes": True,
        "lockedPredictionCount": 0,
        "lockedStatusCount": 0,
        "noPredictionDataCount": 0,
        "lockStatusComplete": False,
        "canonicalPredictionComplete": False,
        "operationalDefect": False,
        "perGameLockInstallation": {
            "ok": True,
            "fixVersion": subject.EXPECTED_FIX_VERSION,
            "officialScheduleAuthorityRequired": True,
            "selectionLockIndependentOfTrainingVector": True,
        },
        "mlLockVectorPreservation": {
            "selectionLockIndependentOfTrainingVector": True,
        },
        "perGameStatus": [
            {
                "officialGamePk": "123",
                "gameId": "mlb_statsapi:123",
                "commenceTime": "2026-08-04T23:00:00+00:00",
                "predictionLockAtUtc": "2026-08-04T22:15:00+00:00",
                "lockStatus": "NOT_YET_LOCKED",
            }
        ],
    }


class _Lambda:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        response.setdefault("ResponseMetadata", {"RequestId": "request-1"})
        response.setdefault("ExecutedVersion", "$LATEST")
        return response


def _response(payload=None, *, status=200, encoded=False, function_error=None):
    body = json.dumps(payload if payload is not None else _payload())
    if encoded:
        body = base64.b64encode(body.encode()).decode()
    raw = {
        "statusCode": status,
        "isBase64Encoded": encoded,
        "body": body,
    }
    value = {"Payload": io.BytesIO(json.dumps(raw).encode())}
    if function_error:
        value["FunctionError"] = function_error
    return value


def test_direct_status_warmup_accepts_valid_json(monkeypatch):
    client = _Lambda([_response()])
    monkeypatch.setattr(subject.boto3, "client", lambda *args, **kwargs: client)

    payload, invocation = subject.invoke(
        function_name="lock-function",
        region="us-east-1",
        attempts=1,
        delay_seconds=0,
    )

    assert payload["officialScheduleBacked"] is True
    assert invocation["statusCode"] == 200
    assert invocation["attempt"] == 1
    assert invocation["gameCount"] == 1
    event = json.loads(client.calls[0]["Payload"].decode())
    assert event["httpMethod"] == "GET"
    assert event["rawPath"] == "/v1/mlb/locks/status"


def test_direct_status_warmup_decodes_binary_media_envelope(monkeypatch):
    client = _Lambda([_response(encoded=True)])
    monkeypatch.setattr(subject.boto3, "client", lambda *args, **kwargs: client)

    payload, _ = subject.invoke(
        function_name="lock-function",
        region="us-east-1",
        attempts=1,
        delay_seconds=0,
    )

    assert payload["perGameStatus"][0]["lockStatus"] == "NOT_YET_LOCKED"


def test_direct_status_warmup_retries_transient_application_failure(monkeypatch):
    client = _Lambda([_response({"ok": False}, status=503), _response()])
    monkeypatch.setattr(subject.boto3, "client", lambda *args, **kwargs: client)
    monkeypatch.setattr(subject.time, "sleep", lambda *_: None)

    _, invocation = subject.invoke(
        function_name="lock-function",
        region="us-east-1",
        attempts=2,
        delay_seconds=0,
    )

    assert invocation["attempt"] == 2
    assert len(client.calls) == 2


def test_direct_status_warmup_rejects_non_official_or_incomplete_payload(monkeypatch):
    invalid = _payload()
    invalid["officialScheduleBacked"] = False
    client = _Lambda([_response(invalid)])
    monkeypatch.setattr(subject.boto3, "client", lambda *args, **kwargs: client)

    with pytest.raises(RuntimeError, match="official-schedule-backed"):
        subject.invoke(
            function_name="lock-function",
            region="us-east-1",
            attempts=1,
            delay_seconds=0,
        )
