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
                "lockStatusComplete": True,
                "officialScheduleBacked": True,
                "officialScheduleGameCount": game_count,
                "perGameLockProgress": {
                    "officialScheduleBacked": True,
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


def test_reconcile_uses_only_protected_lock_and_settlement_lambdas():
    lambda_client = FakeLambda(
        [
            lock_result("2026-08-03"),
            settlement_result("2026-08-03"),
            lock_result("2026-08-04"),
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
    assert result["directTableWrite"] is False
    assert result["postStartPredictionCreationAllowed"] is False
    assert result["immutablePredictionRewriteAllowed"] is False
    assert result["promotionAuthorityChanged"] is False
    assert result["productionAuthorityChanged"] is False
    assert [call["FunctionName"] for call in lambda_client.invocations] == [
        "physical-MLBDailyPickLockFunction",
        "physical-MLBResultsSchedulerFunction",
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
        "sport": "mlb",
        "run": "prospective_backlog_settlement",
        "slate_date": "2026-08-03",
        "days_from": 0,
    }


def test_nonempty_slate_requires_exact_terminal_coverage():
    payload = json.loads(lock_result("2026-08-03")["body"])
    payload["perGameLockProgress"]["lockOutcomeCount"] = 14

    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_slate_terminal_coverage_incomplete",
    ):
        subject.validate_lock_result(payload, "2026-08-03")


def test_candidate_and_terminal_counts_must_reconcile():
    payload = json.loads(lock_result("2026-08-03")["body"])
    payload["perGameLockProgress"]["noPredictionDataCount"] = 4

    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_slate_terminal_counts_inconsistent",
    ):
        subject.validate_lock_result(payload, "2026-08-03")


def test_unresolved_missed_game_fails_closed():
    payload = json.loads(lock_result("2026-08-03")["body"])
    payload["perGameLockProgress"]["missedCount"] = 1

    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_slate_still_unresolved",
    ):
        subject.validate_lock_result(payload, "2026-08-03")


def test_zero_game_date_requires_official_zero_game_proof():
    payload = {
        "ok": True,
        "sport": "mlb",
        "slateDateEt": "2026-08-03",
        "officialScheduleBacked": True,
        "officialScheduleGameCount": 0,
        "perGameLockProgress": {
            "officialScheduleBacked": True,
            "manifestGameCount": 0,
            "games": [],
        },
    }

    result = subject.validate_lock_result(payload, "2026-08-03")

    assert result["offDay"] is True
    payload.pop("officialScheduleGameCount")
    with pytest.raises(
        subject.ReconciliationError,
        match="official_zero_game_slate_unproven",
    ):
        subject.validate_lock_result(payload, "2026-08-03")


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
    assert '"slate_date": slate_date' in source
    assert "postStartPredictionCreationAllowed" in source
    assert "productionAuthorityChanged" in source
