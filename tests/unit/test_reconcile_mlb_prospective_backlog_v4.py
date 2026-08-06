from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as base
import reconcile_mlb_prospective_backlog_v4 as subject


class FakeCloudFormation:
    def describe_stack_resource(self, *, StackName, LogicalResourceId):
        assert StackName == "stack"
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": f"physical-{LogicalResourceId}"
            }
        }


class FakeLambda:
    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {
            "Environment": {
                "Variables": {
                    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-03T04:00:00+00:00"
                }
            }
        }


def client_error(code="TooManyRequestsException", retry_after="7"):
    return ClientError(
        {
            "Error": {"Code": code, "Message": "Rate Exceeded"},
            "ResponseMetadata": {"HTTPHeaders": {"retry-after": retry_after}},
        },
        "Invoke",
    )


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


def settlement(slate_date):
    return {
        "ok": True,
        "slateDateEt": slate_date,
        "slateFinalized": True,
        "settledLabelCount": 10,
    }


def mutation(slate_date):
    return {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": slate_date,
        "perGameLockProgress": {
            "manifestGameCount": 12,
            "canonicalCount": 8,
            "noPredictionDataCount": 4,
            "lockOutcomeCount": 12,
            "missedCount": 0,
            "dueMissingCount": 0,
        },
    }


def test_throttle_is_retried_idempotently(monkeypatch):
    calls = []
    sleeps = []

    def invoke(client, function, event):
        calls.append((client, function, event))
        if len(calls) < 3:
            raise client_error(retry_after="7")
        return {"ok": True}

    monkeypatch.setattr(base, "invoke_json", invoke)
    result = subject.invoke_json_with_backpressure(
        "client",
        "function",
        {"request": "same"},
        sleep=sleeps.append,
        max_attempts=4,
    )

    assert result == {"ok": True}
    assert len(calls) == 3
    assert [call[2] for call in calls] == [{"request": "same"}] * 3
    assert sleeps == [7, 10]


def test_nonretryable_client_error_fails_immediately(monkeypatch):
    calls = []

    def invoke(*args):
        calls.append(args)
        raise client_error(code="AccessDeniedException")

    monkeypatch.setattr(base, "invoke_json", invoke)
    with pytest.raises(ClientError):
        subject.invoke_json_with_backpressure(
            "client", "function", {}, sleep=lambda _: None
        )
    assert len(calls) == 1


def test_retry_exhaustion_is_explicit(monkeypatch):
    monkeypatch.setattr(
        base,
        "invoke_json",
        lambda *args: (_ for _ in ()).throw(client_error()),
    )
    with pytest.raises(
        base.ReconciliationError,
        match="lambda_backpressure_retry_exhausted",
    ):
        subject.invoke_json_with_backpressure(
            "client",
            "function",
            {},
            sleep=lambda _: None,
            max_attempts=2,
        )


def test_complete_official_status_skips_heavy_lock_mutation():
    calls = []

    def invoke(client, function, event):
        del client
        calls.append((function, event))
        slate = event.get("queryStringParameters", {}).get("date") or event.get(
            "slate_date"
        )
        if event.get("httpMethod") == "GET":
            return official_status(slate)
        if event.get("run") == "prospective_backlog_settlement_v4":
            return settlement(slate)
        raise AssertionError(f"unexpected protected mutation: {event}")

    result = subject.reconcile(
        FakeCloudFormation(),
        FakeLambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc),
        invoke=invoke,
    )

    assert result["reconciledSlateCount"] == 2
    assert all(
        row["mutationSkippedBecauseOfficialStatusComplete"] is True
        for row in result["slates"]
    )
    assert all(row["protectedLockReplay"] is False for row in result["slates"])
    assert [event.get("httpMethod") for _, event in calls].count("GET") == 2
    assert not any(event.get("force") is True for _, event in calls)


def test_incomplete_status_uses_one_protected_mutation_then_readback():
    calls = []
    statuses = [
        official_status("2026-08-03", canonical=10, terminal=4),
        official_status("2026-08-03"),
    ]

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        if event.get("httpMethod") == "GET":
            return statuses.pop(0)
        if event.get("force") is True:
            return mutation("2026-08-03")
        if event.get("run") == "prospective_backlog_settlement_v4":
            return settlement("2026-08-03")
        raise AssertionError(event)

    result = subject.reconcile(
        FakeCloudFormation(),
        FakeLambda(),
        stack_name="stack",
        now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
        invoke=invoke,
    )

    assert result["reconciledSlateCount"] == 1
    row = result["slates"][0]
    assert row["protectedLockReplay"] is True
    assert row["mutationSkippedBecauseOfficialStatusComplete"] is False
    assert row["manifestGameCount"] == 15
    assert [event.get("httpMethod") for event in calls].count("GET") == 2
    assert [event.get("force") for event in calls].count(True) == 1


def test_schedule_authority_failure_does_not_trigger_mutation():
    calls = []
    status = official_status("2026-08-03")
    status["officialScheduleBacked"] = False

    def invoke(client, function, event):
        del client, function
        calls.append(event)
        return status

    with pytest.raises(
        base.ReconciliationError,
        match="official_schedule_authority_unproven",
    ):
        subject.reconcile(
            FakeCloudFormation(),
            FakeLambda(),
            stack_name="stack",
            now_utc=datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc),
            invoke=invoke,
        )
    assert len(calls) == 1
    assert calls[0]["httpMethod"] == "GET"


def test_lambda_config_outlives_deployed_lock_timeout():
    config = subject.durable_lambda_config()
    assert config.connect_timeout == 10
    assert config.read_timeout == 420
    assert config.retries["total_max_attempts"] == 1
    assert "max_attempts" not in config.retries


def test_source_has_no_storage_prediction_or_authority_writer():
    source = (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog_v4.py"
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
    assert "statusFirst" in source
    assert "TooManyRequestsException" in source
    assert "postStartPredictionCreationAllowed" in source
