from __future__ import annotations

import base64
import json
from pathlib import Path

from scripts import diagnose_mlb_trainer_deploy_health as diagnostic


def test_redact_removes_nested_secrets_and_bounds_content() -> None:
    value = {
        "apiKey": "provider-secret",
        "nested": {
            "AWS_SESSION_TOKEN": "session-secret",
            "Authorization": "Bearer visible-token",
            "safe": "https://example.test/path?apiKey=query-secret&other=1",
        },
        "long": "x" * (diagnostic.MAX_STRING_LENGTH + 50),
    }

    redacted = diagnostic.redact(value)
    serialized = json.dumps(redacted, sort_keys=True)

    assert redacted["apiKey"] == diagnostic.REDACTED
    assert redacted["nested"]["AWS_SESSION_TOKEN"] == diagnostic.REDACTED
    assert redacted["nested"]["Authorization"] == diagnostic.REDACTED
    assert "query-secret" not in serialized
    assert "provider-secret" not in serialized
    assert "session-secret" not in serialized
    assert redacted["long"].endswith("...[TRUNCATED]")


def test_build_report_preserves_fail_closed_training_evidence() -> None:
    report = diagnostic.build_report(
        training={
            "ok": False,
            "status": "TRAINING_INVOCATION_FAILED",
            "executionMode": "training",
            "runId": "run-1",
            "failure": {
                "type": "TrainingContractError",
                "code": "TRAINING_CONTRACT_ERROR",
                "message": "MLB ML training contract validation failed",
            },
            "acceptedRowCount": 0,
            "rejectedRowCount": 15,
            "rejectionReasonCounts": {"unsafe": 15},
            "productionAuthorityChanged": False,
            "secret": "must-not-survive",
        },
        training_parse_error=None,
        training_invocation={"StatusCode": 200, "ExecutedVersion": "$LATEST"},
        training_invocation_parse_error=None,
        status={
            "ok": False,
            "manifest": {
                "phase": "ACCUMULATING_TRAIN",
                "revision": 4,
                "manifestDigest": "digest",
            },
            "trainingHealth": {
                "ok": False,
                "errors": ["latest_status_not_ok"],
                "executionMode": "training",
                "deploymentIdentityMatches": True,
                "latestRun": {
                    "ok": False,
                    "status": "TRAINING_INVOCATION_FAILED",
                    "acceptedRowCount": 0,
                    "rejectedRowCount": 15,
                    "productionAuthorityChanged": False,
                },
            },
            "selectionCaptureHealth": {
                "ok": True,
                "errors": [],
                "executionMode": "selection_capture",
                "deploymentIdentityMatches": True,
                "latestRun": {"ok": True, "status": "WAITING_FOR_PERSISTED_CHALLENGER"},
            },
        },
        status_parse_error=None,
        status_invocation={"StatusCode": 200},
        status_invocation_parse_error=None,
        configuration={
            "FunctionName": "trainer",
            "Handler": "mlb_ml_aws_training_v1_compat.lambda_handler",
            "Environment": {
                "Variables": {
                    "ODDS_API_KEY": "must-not-survive",
                    "INQSI_DEPLOY_GIT_SHA": "a" * 40,
                }
            },
        },
        configuration_parse_error=None,
        source_sha="b" * 40,
        workflow_run_id="123",
    )

    assert report["ok"] is False
    assert report["classification"] == (
        "TRAINER_RESPONSE_UNHEALTHY:TRAINING_INVOCATION_FAILED"
    )
    assert report["trainingResponse"]["acceptedRowCount"] == 0
    assert report["trainingResponse"]["rejectionReasonCounts"] == {"unsafe": 15}
    assert report["statusAfter"]["manifest"]["phase"] == "ACCUMULATING_TRAIN"
    assert report["productionAuthorityChanged"] is False
    assert report["lambdaConfiguration"]["environment"] == {
        "variableNames": ["INQSI_DEPLOY_GIT_SHA", "ODDS_API_KEY"],
        "valuesRedacted": True,
    }
    assert "must-not-survive" not in json.dumps(report, sort_keys=True)


def test_function_error_classification_decodes_and_redacts_log_tail() -> None:
    log_tail = base64.b64encode(
        b"START RequestId: x\nAWS_SESSION_TOKEN=top-secret\nTrainingContractError: bad row\n"
    ).decode("ascii")
    report = diagnostic.build_report(
        training={
            "errorType": "TrainingContractError",
            "errorMessage": "validation failed",
            "stackTrace": ["safe.py:1"],
        },
        training_parse_error=None,
        training_invocation={
            "StatusCode": 200,
            "FunctionError": "Unhandled",
            "ExecutedVersion": "$LATEST",
            "LogResult": log_tail,
        },
        training_invocation_parse_error=None,
        status={"ok": False},
        status_parse_error=None,
        status_invocation={"StatusCode": 200},
        status_invocation_parse_error=None,
        configuration={},
        configuration_parse_error=None,
        source_sha="c" * 40,
        workflow_run_id="456",
    )

    assert report["classification"] == "TRAINER_LAMBDA_FUNCTION_ERROR"
    assert report["trainingResponse"]["errorType"] == "TrainingContractError"
    log = report["trainingInvocation"]["redactedLogTail"]
    assert "TrainingContractError: bad row" in log
    assert "top-secret" not in log
    assert report["ok"] is False


def test_main_writes_valid_atomic_report(tmp_path: Path) -> None:
    training = tmp_path / "training.json"
    invocation = tmp_path / "training-invocation.json"
    status = tmp_path / "status.json"
    status_invocation = tmp_path / "status-invocation.json"
    configuration = tmp_path / "configuration.json"
    output = tmp_path / "report.json"

    training.write_text('{"ok":true,"status":"ACCUMULATING_TRAIN"}\n', encoding="utf-8")
    invocation.write_text('{"StatusCode":200}\n', encoding="utf-8")
    status.write_text('{"ok":true}\n', encoding="utf-8")
    status_invocation.write_text('{"StatusCode":200}\n', encoding="utf-8")
    configuration.write_text(
        '{"FunctionName":"trainer","Environment":{"Variables":{"SECRET":"x"}}}\n',
        encoding="utf-8",
    )

    assert diagnostic.main(
        [
            "--training-response",
            str(training),
            "--training-invocation",
            str(invocation),
            "--status-response",
            str(status),
            "--status-invocation",
            str(status_invocation),
            "--configuration",
            str(configuration),
            "--output",
            str(output),
            "--source-sha",
            "d" * 40,
            "--workflow-run-id",
            "789",
        ]
    ) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["classification"] == "TRAINER_HEALTHY"
    assert report["secretExposed"] is False
    assert not output.with_name(f"{output.name}.tmp").exists()
