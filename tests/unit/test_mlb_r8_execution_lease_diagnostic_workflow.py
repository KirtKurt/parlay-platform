from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (
    ROOT
    / ".github"
    / "workflows"
    / "inspect-mlb-r8-execution-leases-read-only.yml"
)
EXPERIMENT_PK = (
    "MLB_ML_EXPERIMENT#V2#mlb-v2-2026-07-21-future-prospective-r2"
)
EXPECTED_SORT_KEYS = {
    "EXECUTION_LEASE#STATE_MUTATION",
    "EXECUTION_LEASE#SELECTION_CAPTURE",
    "EXECUTION_LEASE",
}
EXPECTED_PROJECTION = (
    "record_type,#version,lease_domain,lease_owner,execution_mode,"
    "acquired_at,lease_expires_at,lease_expires_at_epoch"
)


def _source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _document() -> dict:
    return yaml.load(_source(), Loader=yaml.BaseLoader)


def _diagnostic_step() -> dict:
    steps = _document()["jobs"]["inspect"]["steps"]
    return next(
        step
        for step in steps
        if step.get("name") == "Read and sanitize exact MLB R8 execution leases"
    )


def _diagnostic_program() -> str:
    run = _diagnostic_step()["run"]
    return run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]


def test_workflow_is_manual_only_with_minimal_permissions_and_short_timeout() -> None:
    document = _document()

    assert set(document["on"]) == {"workflow_dispatch"}
    assert document["permissions"] == {"contents": "read"}
    assert int(document["jobs"]["inspect"]["timeout-minutes"]) <= 5
    source = _source()
    assert "\n  push:" not in source
    assert "\n  schedule:" not in source
    assert "\n  pull_request:" not in source
    assert "workflow_call:" not in source


def test_workflow_reads_only_three_exact_keys_with_an_explicit_projection() -> None:
    source = _source()

    assert source.count(EXPERIMENT_PK) == 1
    assert source.count('"EXECUTION_LEASE#STATE_MUTATION"') == 1
    assert source.count('"EXECUTION_LEASE#SELECTION_CAPTURE"') == 1
    assert source.count('"EXECUTION_LEASE"') == 1
    assert '"dynamodb",\n                  "get-item"' in source
    assert '"--consistent-read"' in source
    assert '"--projection-expression"' in source
    assert EXPECTED_PROJECTION.replace("\n", "") in source.replace("\n", "")
    assert 'attribute_names = {"#version": "version"}' in source
    assert source.count("aws cloudformation describe-stack-resource") == 1
    assert source.count("aws cloudformation describe-stacks") == 1
    assert source.count('"lambda",\n                  "invoke"') == 1
    assert source.count('"logs",\n                      "filter-log-events"') == 1
    assert '{"sport": "mlb", "mode": "status"}' in source
    for sanitized_projection in (
        '"mode": safe_identifier(',
        '"domain": safe_identifier(',
        '"acquiredAt": safe_timestamp(',
        '"expiresAt": safe_timestamp(',
        '"recordType": safe_identifier(',
        '"version": safe_identifier(',
    ):
        assert sanitized_projection in source
    assert "allowed_failure_messages = frozenset(" in source
    assert "safe_failure_message(" in source
    assert 'failure.get("message")' in source


@pytest.mark.parametrize(
    "forbidden",
    (
        "put-item",
        "update-item",
        "delete-item",
        "batch-write-item",
        "transact-write-items",
        "dynamodb scan",
        "dynamodb query",
        "invoke_mlb_trainer",
        '"mode":"scheduled"',
        '"mode": "scheduled"',
        '"mode":"training"',
        '"mode": "training"',
        "get-function-configuration",
        "get-function",
        "list-functions",
        "put-log-events",
        "create-log-group",
        "delete-log-group",
        "put-retention-policy",
        "start-query",
        "sam deploy",
        "cloudformation deploy",
        "contents: write",
    ),
)
def test_workflow_has_no_aws_write_or_training_path(forbidden: str) -> None:
    assert forbidden not in _source().lower()


def test_artifact_is_the_single_sanitized_report_not_raw_responses() -> None:
    document = _document()
    steps = document["jobs"]["inspect"]["steps"]
    upload = next(step for step in steps if step.get("uses") == "actions/upload-artifact@v4")

    assert upload["with"]["path"] == "/tmp/mlb-r8-execution-lease-readonly.json"
    assert upload["with"]["if-no-files-found"] == "error"
    source = _source()
    assert "capture_output=True" in source
    assert "completed.stdout" in source
    assert "> /tmp/" not in source
    assert "config.json" not in source
    assert "response.json" not in source
    assert "raw.json" not in source
    assert "status_file.unlink(missing_ok=True)" in source
    assert "raw_path.unlink(missing_ok=True)" in source
    assert "trap 'rm -f" in source
    assert "CLOUDWATCH_LOG_READ_UNAVAILABLE" in source
    assert "CLOUDWATCH_RUN_LOG_MATCH_NOT_FOUND" in source
    assert "get-function-configuration" not in source


def test_runtime_sanitizes_owner_and_emits_only_allowlisted_lease_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    owner = "raw-owner-canary-that-must-never-leak"
    report_path = tmp_path / "sanitized.json"
    monkeypatch.setenv("TABLE_NAME", "read-only-table")
    monkeypatch.setenv("AWS_REGION_VALUE", "us-east-1")
    monkeypatch.setenv(
        "TRAINER_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:trainer-read-only",
    )
    status_raw_path = tmp_path / "raw-status.json"
    cloudwatch_raw_path = tmp_path / "raw-cloudwatch.json"
    monkeypatch.setenv("STATUS_RAW_PATH", str(status_raw_path))
    monkeypatch.setenv("CLOUDWATCH_RAW_PATH", str(cloudwatch_raw_path))
    monkeypatch.setenv("REPORT_PATH", str(report_path))
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        calls.append(command)
        if command[:3] == ["aws", "dynamodb", "get-item"]:
            assert kwargs == {"check": True, "capture_output": True, "text": True}
            return Completed(
                json.dumps(
                    {
                        "Item": {
                            "record_type": {"S": "MLB_EXECUTION_LEASE"},
                            "version": {"N": "2"},
                            "lease_domain": {"S": "STATE_MUTATION"},
                            "lease_owner": {"S": owner},
                            "execution_mode": {"S": "scheduled"},
                            "acquired_at": {"S": "2026-09-02T16:00:00Z"},
                            "lease_expires_at": {
                                "S": "2099-01-01T00:00:00Z"
                            },
                            "lease_expires_at_epoch": {"N": "4070908800"},
                            "arbitrary_secret_field": {"S": "must-not-leak"},
                            "credentials": {"S": "credential-canary"},
                            "environment": {"S": "environment-canary"},
                        }
                    }
                )
            )
        if command[:3] == ["aws", "lambda", "invoke"]:
            assert kwargs == {"check": False, "capture_output": True, "text": True}
            status_path = Path(command[-1])
            status_path.write_text(
                json.dumps(
                    {
                        "trainingHealth": {
                            "deploymentIdentityMatches": True,
                            "arbitraryHealthField": "must-not-leak",
                            "latestRun": {
                                "runId": "12345678-1234-1234-1234-123456789abc",
                                "createdAtUtc": "2026-09-02T16:00:00+00:00",
                                "status": "TRAINING_INVOCATION_FAILED",
                                "acceptedRowCount": 425,
                                "rejectedRowCount": 3,
                                "partitionCounts": {
                                    "train": 300,
                                    "validation": 100,
                                    "prospectiveTest": 25,
                                    "arbitrary": 999,
                                },
                                "failure": {
                                    "type": "TrainingContractError",
                                    "message": (
                                        "api_key=credential-canary "
                                        "arn:aws:lambda:us-east-1:123456789012:"
                                        "function:must-not-leak"
                                    ),
                                    "code": "must-not-leak",
                                },
                                "arbitraryRunField": "must-not-leak",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            return Completed(
                json.dumps({"StatusCode": 200, "ExecutedVersion": "$LATEST"})
            )
        assert command[:3] == ["aws", "logs", "filter-log-events"]
        assert kwargs["check"] is False
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["text"] is True
        raw_output = kwargs["stdout"]
        raw_output.write(
            json.dumps(
                {
                    "events": [
                        {
                            "message": (
                                "12345678-1234-1234-1234-123456789abc "
                                "FrozenPartitionConflict: frozen slate "
                                "2026-07-21 changed\n"
                                '  File "/var/task/mlb_ml_experiment_v2.py", '
                                "line 812, in advance_manifest\n"
                                "provider api_key=must-not-leak"
                            )
                        }
                    ]
                }
            )
        )
        return Completed("")

    monkeypatch.setattr("subprocess.run", fake_run)
    exec(compile(_diagnostic_program(), str(WORKFLOW), "exec"), {})

    report_text = report_path.read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    assert owner not in report_text
    assert owner not in stdout
    assert "must-not-leak" not in report_text
    assert "credential-canary" not in report_text
    assert "environment-canary" not in report_text
    assert "123456789012" not in report_text
    report = json.loads(report_text)
    assert report["readOnly"] is True
    assert len(report["leases"]) == 3
    assert {row["leaseKey"] for row in report["leases"]} == {
        "STATE_MUTATION",
        "SELECTION_CAPTURE",
        "LEGACY_SENTINEL",
    }
    allowed = {
        "leaseKey",
        "present",
        "mode",
        "domain",
        "acquiredAt",
        "expiresAt",
        "expiresAtEpoch",
        "recordType",
        "version",
        "active",
        "secondsRemaining",
        "ownerFingerprint",
    }
    assert all(set(row) == allowed for row in report["leases"])
    expected_fingerprint = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
    assert all(row["ownerFingerprint"] == expected_fingerprint for row in report["leases"])
    assert all(len(row["ownerFingerprint"]) == 16 for row in report["leases"])
    assert report["trainingHealth"] == {
        "deploymentIdentityMatches": True,
        "latestRun": {
            "runId": "12345678-1234-1234-1234-123456789abc",
            "status": "TRAINING_INVOCATION_FAILED",
            "counts": {
                "acceptedRowCount": 425,
                "rejectedRowCount": 3,
                "partitionCounts": {
                    "train": 300,
                    "validation": 100,
                    "prospectiveTest": 25,
                },
            },
            "failure": {
                "type": "TrainingContractError",
                "message": "MLB ML invocation failure details unavailable",
            },
        },
    }
    assert report["logDiscriminator"] == {
        "blocker": None,
        "frozenPartitionConflict": {
            "classification": "FROZEN_SLATE_CHANGED",
            "message": "frozen slate 2026-07-21 changed",
            "stack": [{"file": "mlb_ml_experiment_v2.py", "line": 812}],
        },
    }
    assert not status_raw_path.exists()
    assert not cloudwatch_raw_path.exists()

    assert len(calls) == 5
    dynamodb_calls = [
        command
        for command in calls
        if command[:3] == ["aws", "dynamodb", "get-item"]
    ]
    lambda_calls = [
        command
        for command in calls
        if command[:3] == ["aws", "lambda", "invoke"]
    ]
    logs_calls = [
        command
        for command in calls
        if command[:3] == ["aws", "logs", "filter-log-events"]
    ]
    assert len(dynamodb_calls) == 3
    assert len(lambda_calls) == 1
    assert len(logs_calls) == 1
    observed_sort_keys = set()
    for command in dynamodb_calls:
        assert "--consistent-read" in command
        projection_index = command.index("--projection-expression")
        assert command[projection_index + 1] == EXPECTED_PROJECTION
        key_index = command.index("--key")
        key = json.loads(command[key_index + 1])
        assert key["PK"]["S"] == EXPERIMENT_PK
        observed_sort_keys.add(key["SK"]["S"])
    assert observed_sort_keys == EXPECTED_SORT_KEYS
    status_command = lambda_calls[0]
    payload_index = status_command.index("--payload")
    assert json.loads(status_command[payload_index + 1]) == {
        "sport": "mlb",
        "mode": "status",
    }
    assert "--log-type" not in status_command
    logs_command = logs_calls[0]
    pattern_index = logs_command.index("--filter-pattern")
    assert logs_command[pattern_index + 1] == (
        '"12345678-1234-1234-1234-123456789abc"'
    )
    start_index = logs_command.index("--start-time")
    end_index = logs_command.index("--end-time")
    assert int(logs_command[end_index + 1]) - int(logs_command[start_index + 1]) == 600_000


def test_cloudwatch_read_failure_returns_stable_blocker_and_deletes_raw_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "raw-cloudwatch.json"
    monkeypatch.setenv("CLOUDWATCH_RAW_PATH", str(raw_path))
    monkeypatch.setenv("AWS_REGION_VALUE", "us-east-1")
    monkeypatch.setenv(
        "TRAINER_ARN",
        "arn:aws:lambda:us-east-1:123456789012:function:trainer-read-only",
    )

    class Completed:
        returncode = 254

    def unavailable(command: list[str], **kwargs: object) -> Completed:
        assert command[:3] == ["aws", "logs", "filter-log-events"]
        assert kwargs["check"] is False
        assert kwargs["stderr"] == subprocess.DEVNULL
        return Completed()

    monkeypatch.setattr("subprocess.run", unavailable)
    definitions = _diagnostic_program().split(
        "\nnow_epoch = int(time.time())",
        1,
    )[0]
    namespace: dict[str, object] = {}
    exec(compile(definitions, str(WORKFLOW), "exec"), namespace)
    discriminator = namespace["cloudwatch_discriminator"]

    result, blocker = discriminator(
        {
            "runId": "12345678-1234-1234-1234-123456789abc",
            "createdAtUtc": "2026-09-02T16:00:00+00:00",
        }
    )

    assert result is None
    assert blocker == "CLOUDWATCH_LOG_READ_UNAVAILABLE"
    assert not raw_path.exists()
