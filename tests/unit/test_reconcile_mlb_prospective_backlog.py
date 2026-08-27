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



def lifecycle_rows(*, canonical=10, terminal=5, quarantine=0):
    rows = []
    for index in range(canonical + terminal + quarantine):
        state = (
            "LOCKED_CANONICAL"
            if index < canonical
            else "LOCKED_NO_PREDICTION_DATA"
            if index < canonical + terminal
            else "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
        )
        rows.append(
            {
                "officialGamePk": str(100000 + index),
                "gameIdentity": f"provider:game-{index}",
                "state": state,
                "lockStatus": state,
                "lockedPrediction": state == "LOCKED_CANONICAL",
                "officialPrediction": state == "LOCKED_CANONICAL",
                "playable": state == "LOCKED_CANONICAL",
                "trainingEligible": state == "LOCKED_CANONICAL",
                "accuracyEligible": state == "LOCKED_CANONICAL",
                "wagerAllowed": state == "LOCKED_CANONICAL",
                "predictionAdopted": state == "LOCKED_CANONICAL",
                "operationalDefect": (
                    state
                    == "MISSED_LOCK_VALID_PRELOCK_CANDIDATE_NOT_PROMOTED"
                ),
            }
        )
    return rows

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
                    "games": lifecycle_rows(
                        canonical=canonical,
                        terminal=terminal,
                    ),
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
                "providerManifestFingerprint": "f" * 64,
                "perGameStatus": lifecycle_rows(
                    canonical=canonical,
                    terminal=terminal,
                ),
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
                "status": "CANONICAL_FINAL_LABELS_COMPLETE",
                "authoritativeSettlement": True,
                "legacySettlementAuthority": False,
                "officialGameCount": 15,
                "officialFinalCount": 15,
                "canonicalLockCount": 10,
                "terminalNoPredictionCount": 5,
                "missedLockValidPrelockQuarantineCount": 0,
                "terminalOutcomeCount": 5,
                "terminalExcludedCount": 5,
                "labelWriteCount": 10,
                "rejectedCanonicalLockCount": 0,
                "lockTerminalConflictCount": 0,
                "skippedNotFinalCount": 0,
                "missingCanonicalLockCount": 0,
                "identityRejectionCount": 0,
                "labelConflictCount": 0,
                "rejectedTerminalOutcomes": [],
                "immutablePregameRowsMutated": False,
                "immutablePregameReadbackErrors": [],
                "labelWrites": [
                    {
                        "ok": True,
                        "status": "CREATED",
                        "officialGamePk": str(100000 + index),
                    }
                    for index in range(10)
                ],
                "terminalExclusions": [
                    {
                        "officialGamePk": str(100000 + index),
                        "status": "LOCKED_NO_PREDICTION_DATA",
                        "accuracyEligible": False,
                        "trainingEligible": False,
                        "predictionAdopted": False,
                    }
                    for index in range(10, 15)
                ],
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


def test_settlement_rejects_fractional_counts():
    payload = json.loads(settlement_result("2026-08-04")["body"])
    payload["terminalOutcomeCount"] = 0.5
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_settlement_terminalOutcomeCount_invalid",
    ):
        subject.validate_settlement_result(payload, "2026-08-04")


def test_zero_game_off_day_settlement_is_valid():
    payload = json.loads(settlement_result("2026-08-03")["body"])
    for field in (
        "officialGameCount",
        "officialFinalCount",
        "canonicalLockCount",
        "terminalNoPredictionCount",
        "missedLockValidPrelockQuarantineCount",
        "terminalOutcomeCount",
        "terminalExcludedCount",
        "labelWriteCount",
    ):
        payload[field] = 0
    result = subject.validate_settlement_result(payload, "2026-08-03")
    assert result["finalized"] is True
    assert result["officialGameCount"] == 0


def test_settlement_lock_count_mismatch_fails_closed():
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_settlement_lock_evidence_mismatch",
    ):
        subject.validate_settlement_lock_binding(
            {
                "manifestGameCount": 15,
                "canonicalPredictionCount": 10,
                "terminalNoPredictionCount": 5,
                "missedLockValidPrelockQuarantineCount": 0,
                "terminalExcludedCount": 5,
            },
            {
                "officialGameCount": 15,
                "canonicalLockCount": 10,
                "terminalNoPredictionCount": 4,
                "missedLockValidPrelockQuarantineCount": 1,
                "terminalExcludedCount": 5,
                "terminalOutcomeCount": 5,
            },
        )


def test_settlement_missing_exact_slate_date_fails_closed():
    payload = json.loads(settlement_result("2026-08-04")["body"])
    payload.pop("slateDateEt")
    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_settlement_slate_mismatch",
    ):
        subject.validate_settlement_result(payload, "2026-08-04")


@pytest.mark.parametrize(
    "surface,field",
    [
        ("mutation", "manifestGameCount"),
        ("mutation", "canonicalCount"),
        ("mutation", "noPredictionDataCount"),
        ("mutation", "lockOutcomeCount"),
        ("status", "gameCount"),
        ("status", "officialScheduleGameCount"),
        ("status", "lockedPredictionCount"),
        ("status", "noPredictionDataCount"),
        ("status", "lockedStatusCount"),
    ],
)
def test_lock_evidence_rejects_fractional_counts(surface, field):
    mutation = _payload(lock_result("2026-08-03"))
    status = _payload(status_result("2026-08-03"))
    target = mutation["perGameLockProgress"] if surface == "mutation" else status
    target[field] = 15.5
    with pytest.raises(subject.ReconciliationError, match="invalid"):
        subject.validate_lock_result(mutation, status, "2026-08-03")


def test_same_count_swapped_game_classification_fails_exact_lock_settlement_binding():
    mutation = _payload(lock_result("2026-08-03"))
    status = _payload(status_result("2026-08-03"))
    lock_evidence = subject.validate_lock_result(
        mutation,
        status,
        "2026-08-03",
    )
    payload = json.loads(settlement_result("2026-08-03")["body"])
    payload["labelWrites"][0]["officialGamePk"] = "100010"
    payload["terminalExclusions"][0]["officialGamePk"] = "100000"
    settlement = subject.validate_settlement_result(
        payload,
        "2026-08-03",
    )

    with pytest.raises(
        subject.ReconciliationError,
        match="prospective_settlement_lock_evidence_mismatch",
    ):
        subject.validate_settlement_lock_binding(
            lock_evidence,
            settlement,
        )
