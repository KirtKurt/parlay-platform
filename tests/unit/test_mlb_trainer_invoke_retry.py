from __future__ import annotations

import io
import json
import sys

import pytest
from botocore.exceptions import ClientError
from botocore.exceptions import ReadTimeoutError

from scripts import invoke_mlb_trainer_with_retry as invoke_retry


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "redacted test failure"}},
        "Invoke",
    )


class FakeLambda:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _success(*, function_error=None):
    value = {
        "StatusCode": 200,
        "ExecutedVersion": "$LATEST",
        "Payload": io.BytesIO(b'{"ok":true}'),
        "ResponseMetadata": {"RequestId": "not-persisted"},
    }
    if function_error:
        value["FunctionError"] = function_error
    return value


def _function_error(error_type: str):
    return {
        "StatusCode": 200,
        "ExecutedVersion": "$LATEST",
        "FunctionError": "Unhandled",
        "Payload": io.BytesIO(
            json.dumps({"errorType": error_type, "errorMessage": "redacted"}).encode()
        ),
    }


def test_retries_only_pre_admission_throttling_with_bounded_backoff():
    client = FakeLambda(
        [
            _client_error("TooManyRequestsException"),
            _client_error("TooManyRequestsException"),
            _success(),
        ]
    )
    sleeps = []

    response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload='{"mode":"status"}',
        sleep=sleeps.append,
    )

    assert response == b'{"ok":true}'
    assert metadata == {"StatusCode": 200, "ExecutedVersion": "$LATEST"}
    assert sleeps == [5, 10]
    assert len(client.calls) == 3
    assert all(call["FunctionName"] == "trainer" for call in client.calls)
    assert all(call["InvocationType"] == "RequestResponse" for call in client.calls)
    assert all(call["Payload"] == b'{"mode":"status"}' for call in client.calls)


def test_backoff_is_bounded_at_sixty_seconds():
    assert [invoke_retry._backoff_seconds(attempt) for attempt in range(1, 7)] == [
        5,
        10,
        20,
        40,
        60,
        60,
    ]


def test_default_retry_horizon_outlasts_old_lock_retry_backlog():
    delays = [
        invoke_retry._backoff_seconds(attempt)
        for attempt in range(1, invoke_retry.DEFAULT_MAX_ATTEMPTS)
    ]

    assert invoke_retry.DEFAULT_MAX_ATTEMPTS == 9
    assert delays == [5, 10, 20, 40, 60, 60, 60, 60]
    assert sum(delays) == 315
    assert sum(delays) >= 300


def test_non_retryable_error_fails_immediately():
    client = FakeLambda([_client_error("AccessDeniedException")])

    with pytest.raises(ClientError) as exc_info:
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload="{}",
            sleep=lambda _seconds: pytest.fail("must not sleep"),
        )

    assert invoke_retry._error_code(exc_info.value) == "AccessDeniedException"
    assert len(client.calls) == 1


def test_ambiguous_read_timeout_is_never_retried():
    client = FakeLambda(
        [
            ReadTimeoutError(
                endpoint_url="https://lambda.us-east-1.amazonaws.com",
                error="redacted test timeout",
            )
        ]
    )

    with pytest.raises(ReadTimeoutError):
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload="{}",
            sleep=lambda _seconds: pytest.fail("must not sleep"),
        )

    assert len(client.calls) == 1


def test_throttle_exhaustion_fails_closed_after_exact_attempt_limit():
    client = FakeLambda([_client_error("TooManyRequestsException")] * 3)
    sleeps = []

    with pytest.raises(ClientError):
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload="{}",
            max_attempts=3,
            sleep=sleeps.append,
        )

    assert len(client.calls) == 3
    assert sleeps == [5, 10]


@pytest.mark.parametrize("max_attempts", (0, 10))
def test_attempt_override_cannot_escape_the_bounded_contract(max_attempts):
    with pytest.raises(ValueError, match="between 1 and 9"):
        invoke_retry.invoke_with_retry(
            client=FakeLambda([_success()]),
            function_name="trainer",
            payload="{}",
            max_attempts=max_attempts,
        )


def test_lambda_function_error_is_preserved_for_downstream_verifier():
    client = FakeLambda([_success(function_error="Unhandled")])

    response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload="{}",
        sleep=lambda _seconds: pytest.fail("must not sleep"),
    )

    assert json.loads(response) == {"ok": True}
    assert metadata["StatusCode"] == 200
    assert metadata["FunctionError"] == "Unhandled"
    assert len(client.calls) == 1


def test_rejects_missing_or_non_bytes_response_payload():
    for payload in (None, io.StringIO("{}")):
        response = {"StatusCode": 200}
        if payload is not None:
            response["Payload"] = payload
        with pytest.raises(RuntimeError, match="payload"):
            invoke_retry.invoke_with_retry(
                client=FakeLambda([response]),
                function_name="trainer",
                payload="{}",
            )


def test_main_atomically_replaces_stale_evidence_after_success(
    tmp_path, monkeypatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")
    client = FakeLambda([_success()])
    client_config = {}

    def fake_client(service_name, **kwargs):
        client_config.update({"service_name": service_name, **kwargs})
        return client

    monkeypatch.setattr(invoke_retry.boto3, "client", fake_client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "invoke_mlb_trainer_with_retry.py",
            "--function-name",
            "trainer",
            "--region",
            "us-east-1",
            "--payload",
            '{"mode":"status"}',
            "--response",
            str(response_path),
            "--invocation",
            str(invocation_path),
        ],
    )

    assert invoke_retry.main() == 0
    assert json.loads(response_path.read_text(encoding="utf-8")) == {"ok": True}
    assert json.loads(invocation_path.read_text(encoding="utf-8")) == {
        "ExecutedVersion": "$LATEST",
        "StatusCode": 200,
    }
    assert client_config["service_name"] == "lambda"
    assert client_config["region_name"] == "us-east-1"
    assert client_config["config"].connect_timeout == 10
    assert client_config["config"].read_timeout == 1000
    assert client_config["config"].retries["total_max_attempts"] == 1


def test_main_removes_stale_evidence_before_nonretryable_failure(
    tmp_path, monkeypatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")
    client = FakeLambda([_client_error("AccessDeniedException")])

    monkeypatch.setattr(invoke_retry.boto3, "client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "invoke_mlb_trainer_with_retry.py",
            "--function-name",
            "trainer",
            "--region",
            "us-east-1",
            "--payload",
            "{}",
            "--response",
            str(response_path),
            "--invocation",
            str(invocation_path),
        ],
    )

    with pytest.raises(ClientError):
        invoke_retry.main()

    assert not response_path.exists()
    assert not invocation_path.exists()


def test_retries_exact_execution_lease_contention_under_separate_deadline():
    client = FakeLambda(
        [_function_error("ExecutionLeaseUnavailable"), _success()]
    )
    clock = iter((100.0,))
    sleeps = []

    response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload='{"mode":"scheduled"}',
        retry_execution_lease=True,
        lease_retry_deadline_seconds=1200,
        lease_retry_delay_seconds=20,
        monotonic=lambda: next(clock),
        sleep=sleeps.append,
    )

    assert json.loads(response) == {"ok": True}
    assert metadata["StatusCode"] == 200
    assert sleeps == [20]
    assert len(client.calls) == 2


def test_full_throttle_backoff_cannot_shorten_stale_lease_recovery_window():
    lease_rejections = 48  # 48 * 20s covers the 960-second trainer lease.
    client = FakeLambda(
        [_client_error("TooManyRequestsException")] * 8
        + [
            _function_error("ExecutionLeaseUnavailable")
            for _ in range(lease_rejections)
        ]
        + [_success()]
    )
    clock = {"now": 0.0}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload='{"mode":"scheduled"}',
        retry_execution_lease=True,
        lease_retry_deadline_seconds=1200,
        lease_retry_delay_seconds=20,
        monotonic=lambda: clock["now"],
        sleep=sleep,
    )

    assert json.loads(response) == {"ok": True}
    assert metadata["StatusCode"] == 200
    assert sleeps[:8] == [5, 10, 20, 40, 60, 60, 60, 60]
    assert sleeps[8:] == [20] * lease_rejections
    assert clock["now"] == 315 + 960
    assert len(client.calls) == 8 + lease_rejections + 1


def test_lease_contention_is_not_retried_without_explicit_scope():
    client = FakeLambda([_function_error("ExecutionLeaseUnavailable")])

    response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload='{"mode":"scheduled"}',
        sleep=lambda _seconds: pytest.fail("must not sleep"),
    )

    assert json.loads(response)["errorType"] == "ExecutionLeaseUnavailable"
    assert metadata["FunctionError"] == "Unhandled"
    assert len(client.calls) == 1


@pytest.mark.parametrize("mode", ("status", "manual_review", "unknown"))
def test_lease_retry_rejects_non_scheduler_modes_before_invoke(mode):
    client = FakeLambda([_success()])

    with pytest.raises(
        invoke_retry.DeployInvokeError,
        match="execution_lease_retry_mode_invalid",
    ):
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload=json.dumps({"mode": mode}),
            retry_execution_lease=True,
        )

    assert client.calls == []


def test_other_function_error_is_never_retried_even_when_lease_retry_enabled():
    client = FakeLambda([_function_error("TrainingContractError")])

    _response, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer",
        payload='{"mode":"selection_capture"}',
        retry_execution_lease=True,
        sleep=lambda _seconds: pytest.fail("must not sleep"),
    )

    assert metadata["FunctionError"] == "Unhandled"
    assert len(client.calls) == 1


def test_lease_retry_fails_before_sleep_when_deadline_cannot_fit_delay():
    client = FakeLambda(
        [
            _function_error("ExecutionLeaseUnavailable"),
            _function_error("ExecutionLeaseUnavailable"),
        ]
    )
    clock = iter((100.0, 1281.0))
    sleeps = []

    with pytest.raises(
        invoke_retry.DeployInvokeError,
        match="execution_lease_retry_deadline_exceeded",
    ):
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload='{"mode":"scheduled"}',
            retry_execution_lease=True,
            lease_retry_deadline_seconds=1200,
            lease_retry_delay_seconds=20,
            monotonic=lambda: next(clock),
            sleep=sleeps.append,
        )

    assert sleeps == [20]
    assert len(client.calls) == 2


def test_status_request_is_bound_to_validated_result_run_ids():
    assert invoke_retry.build_status_request(
        {"executionMode": "training", "runId": "training:abc-1"},
        {
            "executionMode": "selection_capture",
            "runId": "capture:abc-2",
        },
    ) == {
        "mode": "status",
        "trainingRunId": "training:abc-1",
        "selectionCaptureRunId": "capture:abc-2",
    }

    with pytest.raises(invoke_retry.DeployInvokeError, match="training_run_id_invalid"):
        invoke_retry.build_status_request(
            {"executionMode": "training", "runId": "bad run id"},
            {"executionMode": "selection_capture", "runId": "capture-1"},
        )


def test_main_builds_exact_status_payload_from_result_files(tmp_path, monkeypatch):
    training = tmp_path / "training.json"
    selection = tmp_path / "selection.json"
    response_path = tmp_path / "status.json"
    invocation_path = tmp_path / "status-invocation.json"
    training.write_text(
        json.dumps({"executionMode": "training", "runId": "training-1"}),
        encoding="utf-8",
    )
    selection.write_text(
        json.dumps(
            {"executionMode": "selection_capture", "runId": "capture-1"}
        ),
        encoding="utf-8",
    )
    client = FakeLambda([_success()])
    monkeypatch.setattr(invoke_retry.boto3, "client", lambda *_args, **_kwargs: client)

    assert invoke_retry.main(
        [
            "--function-name",
            "trainer",
            "--region",
            "us-east-1",
            "--status-training-result",
            str(training),
            "--status-selection-capture-result",
            str(selection),
            "--response",
            str(response_path),
            "--invocation",
            str(invocation_path),
        ]
    ) == 0

    assert json.loads(client.calls[0]["Payload"]) == {
        "mode": "status",
        "trainingRunId": "training-1",
        "selectionCaptureRunId": "capture-1",
    }
