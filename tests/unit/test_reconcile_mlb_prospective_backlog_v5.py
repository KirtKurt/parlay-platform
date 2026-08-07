from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as v4
import reconcile_mlb_prospective_backlog_v5 as subject


class FakeCloudFormation:
    def describe_stack_resource(self, *, StackName, LogicalResourceId):
        assert StackName == "stack"
        return {"StackResourceDetail": {"PhysicalResourceId": f"physical-{LogicalResourceId}"}}


class ResponseStream(io.BytesIO):
    pass


class FakeLambda:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.events = []

    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {"Environment": {"Variables": {"MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-03T04:00:00+00:00"}}}

    def invoke(self, *, FunctionName, InvocationType, Payload):
        assert InvocationType == "RequestResponse"
        event = json.loads(Payload.decode("utf-8"))
        self.events.append((FunctionName, event))
        response = self.responses.pop(0)
        payload = response if isinstance(response, dict) and ("statusCode" in response or "ok" in response) else dict(response)
        return {"StatusCode": 200, "Payload": ResponseStream(json.dumps(payload).encode("utf-8"))}


def official_status(slate_date, *, games=15, canonical=10, terminal=5):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "gameCount": games,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": base.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "officialScheduleGameCount": games,
        "lockedPredictionCount": canonical,
        "noPredictionDataCount": terminal,
        "lockedStatusCount": canonical + terminal,
        "lockStatusComplete": canonical + terminal == games,
    }


def api_gateway(status, body):
    return {"statusCode": status, "body": json.dumps(body)}


def test_non_2xx_read_only_status_body_is_preserved():
    client = FakeLambda([api_gateway(409, official_status("2026-08-03", canonical=10, terminal=4))])
    result = subject.invoke_json_preserving_status_body(
        client,
        "lock",
        {"httpMethod": "GET", "path": subject.STATUS_PATH, "queryStringParameters": {"date": "2026-08-03"}},
    )
    assert result["ok"] is True
    assert result["lockStatusComplete"] is False
    assert result["_applicationStatusCode"] == 409
    assert result["_nonSuccessStatusBodyPreserved"] is True


def test_non_2xx_mutating_response_remains_fail_closed():
    client = FakeLambda([api_gateway(409, {"ok": False})])
    with pytest.raises(base.ReconciliationError, match="lambda_application_status_not_success"):
        subject.invoke_json_preserving_status_body(
            client,
            "lock",
            {"sport": "mlb", "force": True},
        )


def test_unhealthy_status_body_does_not_trigger_protected_mutation(monkeypatch):
    calls = []
    def fake_invoke(client, function, event):
        del client, function
        calls.append(event)
        return {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"}
    monkeypatch.setattr(base, "invoke_json", fake_invoke)
    with pytest.raises(base.ReconciliationError, match="official_status_unhealthy"):
        v4.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            invoke=fake_invoke,
        )
    assert len(calls) == 1
    assert calls[0]["httpMethod"] == "GET"


def test_v5_preserves_v4_safety_flags(monkeypatch):
    expected = {
        "ok": True,
        "version": v4.VERSION,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "directTableWrite": False,
        "postStartPredictionCreationAllowed": False,
        "immutablePredictionRewriteAllowed": False,
        "promotionAuthorityChanged": False,
    }
    monkeypatch.setattr(v4, "reconcile", lambda *args, **kwargs: dict(expected))
    result = subject.reconcile("cf", "lambda", stack_name="stack")
    assert result["ok"] is True
    assert result["version"] == subject.VERSION
    assert result["readOnlyNonSuccessStatusBodiesPreserved"] is True
    assert result["mutatingNonSuccessStatusesStillFailClosed"] is True
    assert result["productionAuthorityChanged"] is False
    assert result["automaticWagerAllowed"] is False


def test_source_has_no_storage_prediction_or_authority_writer():
    source = (ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v5.py").read_text(encoding="utf-8")
    for forbidden in (
        "put_item(",
        "update_item(",
        "delete_item(",
        "predictedWinner",
        "predicted_winner",
        "INQSI_MLB_ML_AUTO_PROMOTE",
        "productionAuthorityChanged = True",
        "liveInferenceAuthority = True",
    ):
        assert forbidden not in source
    assert "STATUS_PATH" in source
    assert "_nonSuccessStatusBodyPreserved" in source
