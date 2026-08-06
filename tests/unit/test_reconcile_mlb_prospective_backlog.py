from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reconcile_mlb_prospective_backlog as subject


class Payload(io.BytesIO):
    pass


class FakeCloudFormation:
    def describe_stack_resource(self, *, StackName, LogicalResourceId):
        assert StackName == "stack"
        return {
            "StackResourceDetail": {
                "PhysicalResourceId": f"physical-{LogicalResourceId}"
            }
        }


class FakeLambda:
    def __init__(self, responses):
        self.responses = list(responses)
        self.invocations = []

    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == "physical-MLBMLTrainingFunction"
        return {
            "Environment": {
                "Variables": {
                    "MLB_ML_RELEASE_CUTOFF_UTC": "2026-08-03T04:00:00+00:00"
                }
            }
        }

    def invoke(self, *, FunctionName, InvocationType, Payload):
        self.invocations.append(
            {
                "FunctionName": FunctionName,
                "InvocationType": InvocationType,
                "Payload": json.loads(Payload.decode("utf-8")),
            }
        )
        payload = self.responses.pop(0)
        return {
            "StatusCode": 200,
            "Payload": globals()["Payload"](
                json.dumps(payload).encode("utf-8")
            ),
        }


def lock_result(slate_date, *, canonical=10, terminal=5):
    game_count = canonical + terminal
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": slate_date,
                "perGameLockProgress": {
                    "manifestGameCount": game_count,
                    "games": [
                        {"gameIdentity": f"game-{index}"}
                        for index in range(game_count)
                    ],
                    "canonicalCount": canonical,
                    "noPredictionDataCount": terminal,
                    "lockOutcomeCount": game_count,
                    "missedCount": 0,
                    "dueMissingCount": 0,
                },
            }
        ),
    }


def status_result(slate_date, *, canonical=10, terminal=5):
    game_count = canonical + terminal
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "ok": True,
                "sport": "mlb",
                "slateDateEt": slate_date,
                "gameCount": game_count,
                "officialScheduleBacked": True,
                "officialScheduleAuthorityVersion": (
                    subject.OFFICIAL_SCHEDULE_AUTHORITY_VERSION
                ),
                "officialScheduleAuthoritativeStartTimes": True,
                "officialScheduleGameCount": game_count,
                "lockedPredictionCount": canonical,
                "noPredictionDataCount": terminal,
                "lockedStatusCount": game_count,
                "lockStatusComplete": True,
            }
        ),
    }


def settlement_result(slate_date):
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "ok": True,
                "slateDateEt": slate_date,
                "slateFinalized": True,
                "settledLabelCount": 10,
            }
        ),
    }


def test_date_range_is_release_cutoff_through_yesterday_et():
    dates = subject.prospective_slate_dates(
        "2026-08-03T04:00:00+00:00",
        now_utc=datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc),
        max_slate_days=14,
    )
    assert dates == ["2026-08-03", "2026-08-04"]


def test_date_range_is_hard_bounded():
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_backlog_exceeds_bounded_horizon",
    ):
        subject.prospective_slate_dates(
            "2026-07-01T04:00:00+00:00",
            now_utc=datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc),
            max_slate_days=14,
        )


def test_reconcile_binds_protected_replay_to_read_only_official_status():
    lambda_client = FakeLambda(
        [
            lock_result("2026-08-03"),
            status_result("2026-08-03"),
            settlement_result("2026-08-03"),
            lock_result("2026-08-04"),
            status_result("2026-08-04"),
            settlement_result("2026-08-04"),
        ]
    )

    result = subject.reconcile(
        FakeCloudFormation(),
        lambda_client,
        stack_name="stack",
        now_utc=datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["reconciledSlateCount"] == 2
    assert result["readOnlyOfficialStatusProof"] is True
    assert result["directTableWrite"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["immutablePredictionRewriteAllowed"] is False
    assert [call["FunctionName"] for call in lambda_client.invocations] == [
        "physical-MLBDailyPickLockFunction",
        "physical-MLBDailyPickLockFunction",
        "physical-MLBResultsSchedulerFunction",
        "physical-MLBDailyPickLockFunction",
        "physical-MLBDailyPickLockFunction",
        "physical-MLBResultsSchedulerFunction",
    ]
    assert lambda_client.invocations[0]["Payload"] == {
        "sport": "mlb",
        "run": "prospective_terminal_backlog_reconciliation",
        "slateDateEt": "2026-08-03",
        "force": True,
    }
    assert lambda_client.invocations[1]["Payload"] == {
        "httpMethod": "GET",
        "path": "/v1/mlb/locks/status",
        "queryStringParameters": {"date": "2026-08-03"},
    }


def _payload(response):
    return json.loads(response["body"])


def test_mutation_and_read_side_counts_must_match():
    mutation = _payload(lock_result("2026-08-03"))
    status = _payload(status_result("2026-08-03", canonical=9, terminal=6))
    with pytest.raises(
        subject.ReconciliationError,
        match="mutation_and_status_prediction_count_mismatch",
    ):
        subject.validate_lock_result(mutation, status, "2026-08-03")


def test_nonempty_slate_requires_exact_terminal_coverage():
    mutation = _payload(lock_result("2026-08-03"))
    mutation["perGameLockProgress"]["lockOutcomeCount"] = 14
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_slate_terminal_coverage_incomplete",
    ):
        subject.validate_lock_result(
            mutation,
            _payload(status_result("2026-08-03")),
            "2026-08-03",
        )


def test_unresolved_missed_game_fails_closed():
    mutation = _payload(lock_result("2026-08-03"))
    mutation["perGameLockProgress"]["missedCount"] = 1
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_slate_still_unresolved",
    ):
        subject.validate_lock_result(
            mutation,
            _payload(status_result("2026-08-03")),
            "2026-08-03",
        )


def test_official_schedule_proof_is_required_from_read_side():
    status = _payload(status_result("2026-08-03"))
    status["officialScheduleBacked"] = False
    with pytest.raises(
        subject.ReconciliationError,
        match="official_schedule_authority_unproven",
    ):
        subject.validate_lock_result(
            _payload(lock_result("2026-08-03")),
            status,
            "2026-08-03",
        )


def test_zero_game_date_requires_exact_official_zero_game_status():
    mutation = {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-03",
        "perGameLockProgress": {"manifestGameCount": 0, "games": []},
    }
    status = {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-03",
        "gameCount": 0,
        "officialScheduleBacked": True,
        "officialScheduleAuthorityVersion": subject.OFFICIAL_SCHEDULE_AUTHORITY_VERSION,
        "officialScheduleAuthoritativeStartTimes": True,
        "officialScheduleGameCount": 0,
        "lockedPredictionCount": 0,
        "noPredictionDataCount": 0,
        "lockedStatusCount": 0,
        "lockStatusComplete": False,
    }
    result = subject.validate_lock_result(mutation, status, "2026-08-03")
    assert result["offDay"] is True
    assert result["officialStatusReadBound"] is True


def test_lambda_function_error_is_terminal():
    class FunctionErrorLambda(FakeLambda):
        def invoke(self, **kwargs):
            return {
                "StatusCode": 200,
                "FunctionError": "Unhandled",
                "Payload": Payload(b'{"errorType":"RuntimeError"}'),
            }

    with pytest.raises(
        subject.ReconciliationError,
        match="lambda_function_error",
    ):
        subject.invoke_json(FunctionErrorLambda([]), "function", {})


def test_source_contains_no_direct_storage_or_prediction_write_path():
    source = (
        ROOT / "scripts" / "reconcile_mlb_prospective_backlog.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "put_item(",
        "update_item(",
        "delete_item(",
        "batch_writer(",
        "boto3.resource(\"dynamodb\"",
        "boto3.resource('dynamodb'",
        "predictedWinner",
        "predicted_winner",
    )
    assert all(token not in source for token in forbidden)
    assert '"force": True' in source
    assert '"httpMethod": "GET"' in source
    assert "readOnlyOfficialStatusProof" in source
    assert "postStartPredictionCreationAllowed" in source
    assert "productionAuthorityChanged" in source
