from __future__ import annotations

import base64
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
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": f"physical-{LogicalResourceId}"
            }
        }


class ResponseStream(io.BytesIO):
    pass


class FakeLambda:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.events = []
        self.log_types = []

    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {
            "Environment": {
                "Variables": {
                    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-03T04:00:00+00:00"
                }
            }
        }

    def invoke(
        self,
        *,
        FunctionName,
        InvocationType,
        Payload,
        LogType=None,
    ):
        assert InvocationType == "RequestResponse"
        event = json.loads(Payload.decode("utf-8"))
        self.events.append((FunctionName, event))
        self.log_types.append(LogType)
        response = self.responses.pop(0)
        if isinstance(response, dict) and "_invoke" in response:
            envelope = dict(response["_invoke"])
            payload = envelope.pop("payload", {})
            envelope.setdefault("StatusCode", 200)
            envelope["Payload"] = ResponseStream(
                json.dumps(payload).encode("utf-8")
            )
            return envelope
        payload = (
            response
            if isinstance(response, dict)
            and ("statusCode" in response or "ok" in response)
            else dict(response)
        )
        return {
            "StatusCode": 200,
            "Payload": ResponseStream(json.dumps(payload).encode("utf-8")),
        }


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
    client = FakeLambda(
        [api_gateway(409, official_status("2026-08-03", canonical=10, terminal=4))]
    )
    result = subject.invoke_json_preserving_status_body(
        client,
        "lock",
        {
            "httpMethod": "GET",
            "path": subject.STATUS_PATH,
            "queryStringParameters": {"date": "2026-08-03"},
        },
    )
    assert result["ok"] is True
    assert result["lockStatusComplete"] is False
    assert result["_applicationStatusCode"] == 409
    assert result["_nonSuccessStatusBodyPreserved"] is True
    assert client.log_types == ["Tail"]


def test_non_2xx_mutating_response_remains_fail_closed():
    client = FakeLambda([api_gateway(409, {"ok": False})])
    with pytest.raises(
        base.ReconciliationError,
        match="lambda_application_status_not_success",
    ):
        subject.invoke_json_preserving_status_body(
            client,
            "lock",
            {"sport": "mlb", "force": True},
        )


def test_lambda_function_error_preserves_only_redacted_bounded_evidence():
    secret = "top-secret-value"
    log_tail = (
        "START request\n"
        f"apiKey={secret} Authorization: Bearer {secret}\n"
        "Task timed out after 900.00 seconds\n"
    )
    client = FakeLambda(
        [
            {
                "_invoke": {
                    "FunctionError": "Unhandled",
                    "LogResult": base64.b64encode(
                        log_tail.encode("utf-8")
                    ).decode("ascii"),
                    "payload": {
                        "errorType": "Sandbox.Timedout",
                        "errorMessage": (
                            "Task timed out after 900.00 seconds; "
                            f"token={secret}"
                        ),
                        "stackTrace": [f"must-not-appear-{secret}"],
                    },
                }
            }
        ]
    )

    with pytest.raises(base.ReconciliationError) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "physical-MLBDailyPickLockFunction",
            {
                "sport": "mlb",
                "run": subject.TERMINAL_REPLAY_RUN,
                "slateDateEt": "2026-08-04",
                "force": True,
                "adminToken": secret,
            },
        )

    message = str(raised.value)
    assert message.startswith("lambda_function_error:")
    detail = json.loads(message.split(":", 1)[1])
    assert detail == {
        "errorMessage": (
            "Task timed out after 900.00 seconds; token=[REDACTED]"
        ),
        "errorType": "Sandbox.Timedout",
        "eventKind": subject.TERMINAL_REPLAY_RUN,
        "functionError": "Unhandled",
        "functionName": "physical-MLBDailyPickLockFunction",
        "redactedLogTail": (
            "START request\n"
            "apiKey=[REDACTED] Authorization: Bearer [REDACTED]\n"
            "Task timed out after 900.00 seconds\n"
        ),
        "requestPayloadIncluded": False,
        "secretExposed": False,
        "slateDateEt": "2026-08-04",
    }
    assert secret not in message
    assert "adminToken" not in message
    assert "stackTrace" not in message
    assert client.log_types == ["Tail"]


def test_lambda_function_error_invalid_payload_and_log_remain_bounded():
    client = FakeLambda(
        [
            {
                "_invoke": {
                    "FunctionError": "Unhandled",
                    "LogResult": "not-base64",
                    "payload": "not-json-object",
                }
            }
        ]
    )

    with pytest.raises(base.ReconciliationError) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "lock",
            {
                "httpMethod": "GET",
                "path": subject.STATUS_PATH,
                "queryStringParameters": {"date": "2026-08-04"},
            },
        )

    detail = json.loads(str(raised.value).split(":", 1)[1])
    assert detail["eventKind"] == "read_only_lock_status"
    assert detail["slateDateEt"] == "2026-08-04"
    assert detail["logTailParseError"] == "Error"
    assert detail["requestPayloadIncluded"] is False


def test_failed_settlement_exposes_only_bounded_whitelisted_diagnostics():
    client = FakeLambda(
        [
            api_gateway(
                409,
                {
                    "ok": False,
                    "sport": "mlb",
                    "status": "FAILED_CLOSED",
                    "slateDateEt": "2026-08-04",
                    "officialGameCount": 15,
                    "officialFinalCount": 15,
                    "canonicalLockCount": 12,
                    "rejectedCanonicalLockCount": 1,
                    "missingCanonicalLockCount": 2,
                    "identityRejectionCount": 1,
                    "labelConflictCount": 0,
                    "immutablePregameRowsMutated": False,
                    "missingCanonicalLocks": [
                        {
                            "officialGamePk": "123",
                            "reason": "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME",
                            "secretToken": "must-not-appear",
                        },
                        {
                            "officialGamePk": "456",
                            "reason": "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME",
                        },
                    ],
                    "identityRejections": [
                        {
                            "officialGamePk": "789",
                            "reason": "CANONICAL_LOCK_OFFICIAL_FINAL_ORDERED_TEAMS_MISMATCH",
                            "lockedTeams": ["a", "b"],
                            "officialTeams": ["a", "c"],
                        }
                    ],
                    "apiKey": "must-not-appear",
                },
            )
        ]
    )

    with pytest.raises(base.ReconciliationError) as raised:
        subject.invoke_json_preserving_status_body(
            client,
            "settlement",
            {
                "sport": "mlb",
                "slate_date": "2026-08-04",
                "run": "prospective_backlog_settlement_v4",
            },
        )

    message = str(raised.value)
    assert "lambda_application_status_not_success" in message
    detail = json.loads(message.split(":", 1)[1])
    assert detail["applicationStatusCode"] == 409
    assert detail["eventKind"] == "prospective_backlog_settlement_v4"
    assert detail["officialGameCount"] == 15
    assert detail["canonicalLockCount"] == 12
    assert detail["missingCanonicalLockCount"] == 2
    assert detail["missingCanonicalLocksObservedCount"] == 2
    assert detail["missingCanonicalLocksSample"][0] == {
        "officialGamePk": "123",
        "reason": "MISSING_VALID_CANONICAL_LOCK_OR_TERMINAL_OUTCOME",
    }
    assert detail["identityRejectionsSample"][0]["officialGamePk"] == "789"
    assert detail["immutablePregameRowsMutated"] is False
    assert "must-not-appear" not in message
    assert "apiKey" not in message


def test_failure_collection_is_bounded():
    rows = [
        {"officialGamePk": str(index), "reason": "MISSING"}
        for index in range(subject.MAX_DIAGNOSTIC_ITEMS + 4)
    ]
    detail = json.loads(
        subject._safe_application_detail(
            409,
            {
                "status": "FAILED_CLOSED",
                "missingCanonicalLocks": rows,
            },
            {"run": "settlement"},
        )
    )
    assert detail["missingCanonicalLocksObservedCount"] == len(rows)
    assert (
        len(detail["missingCanonicalLocksSample"])
        == subject.MAX_DIAGNOSTIC_ITEMS
    )


def test_unhealthy_status_body_does_not_trigger_protected_mutation(monkeypatch):
    calls = []
    sleeps = []

    def fake_invoke(client, function, event):
        del client, function
        calls.append(event)
        return {"ok": False, "sport": "mlb", "slateDateEt": "2026-08-03"}

    monkeypatch.setattr(base, "invoke_json", fake_invoke)
    with pytest.raises(
        base.ReconciliationError,
        match="official_status_consistency_retry_exhausted:official_status_unhealthy",
    ):
        v4.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            invoke=fake_invoke,
            status_sleep=sleeps.append,
        )
    assert len(calls) == v4.STATUS_CONSISTENCY_MAX_ATTEMPTS
    assert all(call["httpMethod"] == "GET" for call in calls)
    assert not any(call.get("force") is True for call in calls)
    assert sleeps == list(v4.STATUS_CONSISTENCY_RETRY_DELAYS_SECONDS)


def test_protected_replay_uses_same_function_error_adapter(monkeypatch):
    original = base.invoke_json
    observed = []

    def fake_with_backpressure(client, function, event):
        observed.append(base.invoke_json)
        return base.invoke_json(client, function, event)

    monkeypatch.setattr(v4, "invoke_json_with_backpressure", fake_with_backpressure)
    request = subject.DurableTerminalReplayRequired(
        "2026-08-04",
        {"slateDateEt": "2026-08-04"},
    )
    client = FakeLambda(
        [
            {
                "_invoke": {
                    "FunctionError": "Unhandled",
                    "payload": {
                        "errorType": "RuntimeError",
                        "errorMessage": "protected replay failed",
                    },
                }
            }
        ]
    )

    with pytest.raises(base.ReconciliationError, match="lambda_function_error"):
        subject._execute_protected_terminal_replay(
            FakeCloudFormation(),
            client,
            stack_name="stack",
            request=request,
        )

    assert observed == [subject.invoke_json_preserving_status_body]
    assert base.invoke_json is original


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
    assert result["semanticStatusConsistencyRetryInstalled"] is True
    assert result["mutatingNonSuccessStatusesStillFailClosed"] is True
    assert result["mutatingFailureDiagnosticsWhitelisted"] is True
    assert result["lambdaFunctionErrorsRedacted"] is True
    assert result["lambdaFunctionErrorRequestPayloadIncluded"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["automaticWagerAllowed"] is False


def test_source_has_no_storage_prediction_or_authority_writer():
    source = (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v5.py"
    ).read_text(encoding="utf-8")
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
    assert "SAFE_FAILURE_COLLECTIONS" in source
    assert "mutatingFailureDiagnosticsWhitelisted" in source
    assert "LogType=\"Tail\"" in source
    assert "requestPayloadIncluded" in source
    assert "lambdaFunctionErrorsRedacted" in source
    assert "read_official_status_with_consistency_retry" in source
    assert "semanticStatusConsistencyRetryInstalled" in source


def test_recovery_workflow_uses_unique_bounded_dispatch():
    workflow = (
        ROOT
        / ".github"
        / "workflows"
        / "mlb-prospective-backlog-reconcile-v5-once.yml"
    ).read_text(encoding="utf-8")
    trigger = workflow.split("permissions:", 1)[0]

    assert "--max-slate-days 31" in workflow
    assert "request_id:" in trigger
    assert "  workflow_dispatch:" in trigger
    assert "\n  push:" not in trigger
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "directTableWrite" in workflow
    assert "productionAuthorityChanged" in workflow
