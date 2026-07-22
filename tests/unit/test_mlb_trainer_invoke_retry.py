from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from scripts import invoke_mlb_trainer_with_retry as invoke_retry

HELLO_WORLD = Path(__file__).resolve().parents[2] / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_aws_training_v1 as aws_training


def test_retry_lease_message_exactly_matches_runtime_contract() -> None:
    assert invoke_retry.LEASE_ERROR_MESSAGE == (
        aws_training.EXECUTION_LEASE_UNAVAILABLE_MESSAGE
    )


def _client_error(code: str, message: str = "test failure") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": message}},
        "Invoke",
    )


class TrackingStream(io.BytesIO):
    pass


class FailingStream:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False

    def read(self):
        raise self.error

    def close(self) -> None:
        self.closed = True


class NonCloseableStream:
    def read(self):
        return b'{"ok":true}'


class FakeLambda:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response(
    body: bytes = b'{"ok":true}',
    *,
    function_error: str | None = None,
    status_code: int = 200,
    stream=None,
):
    payload_stream = stream if stream is not None else TrackingStream(body)
    value = {
        "StatusCode": status_code,
        "ExecutedVersion": "$LATEST",
        "Payload": payload_stream,
        "ResponseMetadata": {"RequestId": "must-not-be-persisted"},
    }
    if function_error is not None:
        value["FunctionError"] = function_error
    return value, payload_stream


def _lease_contention(
    *,
    error_type: str = invoke_retry.LEASE_ERROR_TYPE,
    error_message: str = invoke_retry.LEASE_ERROR_MESSAGE,
    function_error: str = "Unhandled",
):
    return _response(
        json.dumps(
            {
                "errorType": error_type,
                "errorMessage": error_message,
                "requestId": "redacted-test-request",
                "stackTrace": ["redacted"],
            }
        ).encode("utf-8"),
        function_error=function_error,
    )


def _invoke(client, *, mode="scheduled", sleep=None):
    kwargs = {
        "client": client,
        "function_name": "trainer",
        "payload": json.dumps({"sport": "mlb", "mode": mode}),
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return invoke_retry.invoke_with_retry(**kwargs)


def test_backoff_is_deterministic_capped_and_each_class_has_975_second_horizon():
    delays = [invoke_retry._backoff_seconds(value) for value in range(1, 20)]

    assert delays[:5] == [5, 10, 20, 40, 60]
    assert delays[-1] == 60
    assert sum(delays) == 975
    assert invoke_retry.MAX_FAILURES_PER_RETRY_CLASS == 20


@pytest.mark.parametrize(
    ("code", "message"),
    (
        ("TooManyRequests", "test"),
        ("TooManyRequestsException", "test"),
        ("ConcurrentInvocationLimit", "test"),
        ("ConcurrentInvocationLimitExceeded", "test"),
        ("OtherCapacityCode", "Rate Exceeded."),
    ),
)
def test_retries_only_explicit_pre_admission_capacity_errors(code, message):
    success, success_stream = _response()
    client = FakeLambda([_client_error(code, message), success])
    sleeps = []

    body, metadata = _invoke(client, sleep=sleeps.append)

    assert json.loads(body) == {"ok": True}
    assert sleeps == [5]
    assert len(client.calls) == 2
    assert success_stream.closed is True
    assert metadata["InvocationRetryControl"] == {
        "version": invoke_retry.RETRY_CONTROL_VERSION,
        "mode": "scheduled",
        "retryEnabled": True,
        "preAdmissionCapacityRetryEnabled": True,
        "executionLeaseRetryEnabled": True,
        "maxFailuresPerClass": 20,
        "invocationAttempts": 2,
        "preAdmissionCapacityFailures": 1,
        "executionLeaseContentionFailures": 0,
    }


def test_pre_admission_and_lease_contention_have_independent_full_budgets():
    outcomes = []
    for _index in range(19):
        outcomes.append(_client_error("TooManyRequestsException"))
        outcomes.append(_lease_contention()[0])
    success, _stream = _response()
    outcomes.append(success)
    client = FakeLambda(outcomes)
    sleeps = []

    _body, metadata = _invoke(client, sleep=sleeps.append)

    assert len(client.calls) == 39
    assert sleeps[::2] == [
        invoke_retry._backoff_seconds(value) for value in range(1, 20)
    ]
    assert sleeps[1::2] == [
        invoke_retry._backoff_seconds(value) for value in range(1, 20)
    ]
    assert sum(sleeps[::2]) == 975
    assert sum(sleeps[1::2]) == 975
    assert metadata["InvocationRetryControl"]["preAdmissionCapacityFailures"] == 19
    assert metadata["InvocationRetryControl"][
        "executionLeaseContentionFailures"
    ] == 19


@pytest.mark.parametrize("mode", ("scheduled", "status"))
def test_pre_admission_capacity_exhaustion_raises_custom_error_after_20_failures(
    mode,
):
    client = FakeLambda(
        [_client_error("TooManyRequestsException") for _index in range(20)]
    )
    sleeps = []

    with pytest.raises(invoke_retry.TrainerInvokeRetryExhausted) as exc_info:
        _invoke(client, mode=mode, sleep=sleeps.append)

    assert exc_info.value.retry_class == "pre_admission_capacity"
    assert exc_info.value.invocation_attempts == 20
    assert exc_info.value.pre_admission_failures == 20
    assert exc_info.value.lease_contention_failures == 0
    assert len(client.calls) == 20
    assert sum(sleeps) == 975


@pytest.mark.parametrize(
    "mode", ("scheduled", "selection_capture", "manual_review")
)
def test_exact_admitted_lease_contention_retries_only_for_mutating_modes(mode):
    contention, contention_stream = _lease_contention()
    success, success_stream = _response()
    client = FakeLambda([contention, success])
    sleeps = []

    _body, metadata = _invoke(client, mode=mode, sleep=sleeps.append)

    assert sleeps == [5]
    assert len(client.calls) == 2
    assert contention_stream.closed is True
    assert success_stream.closed is True
    retry = metadata["InvocationRetryControl"]
    assert retry["mode"] == mode
    assert retry["preAdmissionCapacityFailures"] == 0
    assert retry["executionLeaseContentionFailures"] == 1


def test_execution_lease_exhaustion_has_its_own_custom_error_and_975_seconds():
    client = FakeLambda([_lease_contention()[0] for _index in range(20)])
    sleeps = []

    with pytest.raises(invoke_retry.TrainerInvokeRetryExhausted) as exc_info:
        _invoke(client, mode="selection_capture", sleep=sleeps.append)

    assert exc_info.value.retry_class == "execution_lease_contention"
    assert exc_info.value.invocation_attempts == 20
    assert exc_info.value.pre_admission_failures == 0
    assert exc_info.value.lease_contention_failures == 20
    assert len(client.calls) == 20
    assert sum(sleeps) == 975


def test_status_retries_only_proven_pre_admission_capacity_rejection():
    success, stream = _response()
    client = FakeLambda([_client_error("TooManyRequestsException"), success])
    sleeps = []

    _body, metadata = _invoke(client, mode="status", sleep=sleeps.append)

    assert sleeps == [5]
    assert len(client.calls) == 2
    assert stream.closed is True
    retry = metadata["InvocationRetryControl"]
    assert retry["retryEnabled"] is True
    assert retry["preAdmissionCapacityRetryEnabled"] is True
    assert retry["executionLeaseRetryEnabled"] is False
    assert retry["preAdmissionCapacityFailures"] == 1
    assert retry["executionLeaseContentionFailures"] == 0


@pytest.mark.parametrize(
    "response",
    (
        _lease_contention()[0],
        _response(
            b'{"errorType":"TrainingContractError","errorMessage":"validation"}',
            function_error="Unhandled",
        )[0],
    ),
)
def test_status_is_one_shot_for_any_admitted_function_error(response):
    client = FakeLambda([response])

    with pytest.raises(invoke_retry.TrainerInvocationFunctionError):
        _invoke(
            client,
            mode="status",
            sleep=lambda _seconds: pytest.fail("status must not sleep"),
        )

    assert len(client.calls) == 1
    assert response["Payload"].closed is True


@pytest.mark.parametrize(
    "response",
    (
        _lease_contention(error_message="near-match lease error")[0],
        _lease_contention(error_type="ExecutionLeaseUnavailableV2")[0],
        _lease_contention(function_error="Handled")[0],
        _response(b"not-json", function_error="Unhandled")[0],
        _response(
            b'{"errorType":"TrainingContractError","errorMessage":"validation"}',
            function_error="Unhandled",
        )[0],
    ),
)
def test_generic_near_match_and_malformed_function_errors_are_never_retried(
    response,
):
    client = FakeLambda([response])

    with pytest.raises(invoke_retry.TrainerInvocationFunctionError):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("function error must not sleep"),
        )

    assert len(client.calls) == 1
    assert response["Payload"].closed is True


def test_exact_lease_payload_with_non_200_status_is_terminal_without_retry():
    response, stream = _response(
        json.dumps(
            {
                "errorType": invoke_retry.LEASE_ERROR_TYPE,
                "errorMessage": invoke_retry.LEASE_ERROR_MESSAGE,
            }
        ).encode("utf-8"),
        function_error="Unhandled",
        status_code=500,
    )
    client = FakeLambda([response])

    with pytest.raises(invoke_retry.TrainerInvocationResponseError):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("non-200 response must not sleep"),
        )

    assert len(client.calls) == 1
    assert stream.closed is True


@pytest.mark.parametrize(
    "error",
    (
        _client_error("AccessDeniedException"),
        _client_error("ValidationException"),
        _client_error("UnrecognizedClientException"),
        _client_error("OtherCapacityCode", "Rate Exceeded for another reason"),
    ),
)
def test_auth_validation_and_generic_client_errors_are_never_retried(error):
    client = FakeLambda([error])

    with pytest.raises(ClientError) as exc_info:
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("terminal error must not sleep"),
        )

    assert exc_info.value is error
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "error",
    (
        ReadTimeoutError(endpoint_url="https://lambda.test", error="timeout"),
        ConnectTimeoutError(endpoint_url="https://lambda.test", error="timeout"),
        EndpointConnectionError(endpoint_url="https://lambda.test"),
    ),
)
def test_ambiguous_transport_errors_are_never_retried(error):
    client = FakeLambda([error])

    with pytest.raises(type(error)):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("transport error must not sleep"),
        )

    assert len(client.calls) == 1


def test_ambiguous_payload_read_timeout_is_not_retried_and_stream_is_closed():
    error = ReadTimeoutError(endpoint_url="https://lambda.test", error="timeout")
    stream = FailingStream(error)
    response, _ = _response(stream=stream)
    client = FakeLambda([response])

    with pytest.raises(ReadTimeoutError):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("ambiguous read must not sleep"),
        )

    assert len(client.calls) == 1
    assert stream.closed is True


@pytest.mark.parametrize(
    "response",
    (
        _response(b'{"ok":false}')[0],
        _response(b"not-json")[0],
        _response(b"[]")[0],
        _response(status_code=500)[0],
    ),
)
def test_noncanonical_success_evidence_is_rejected_without_retry(response):
    client = FakeLambda([response])

    with pytest.raises(invoke_retry.TrainerInvocationResponseError):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("bad evidence must not sleep"),
        )

    assert len(client.calls) == 1
    assert response["Payload"].closed is True


@pytest.mark.parametrize(
    "response",
    (
        {"StatusCode": 200},
        {"StatusCode": 200, "Payload": object()},
        {"StatusCode": 200, "Payload": NonCloseableStream()},
        {"StatusCode": 200, "Payload": io.StringIO('{"ok":true}')},
    ),
)
def test_missing_noncloseable_or_nonbytes_stream_is_terminal(response):
    client = FakeLambda([response])

    with pytest.raises(invoke_retry.TrainerInvocationResponseError):
        _invoke(
            client,
            sleep=lambda _seconds: pytest.fail("bad stream must not sleep"),
        )

    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "payload",
    (
        "not-json",
        "[]",
        "{}",
        '{"mode":"unknown"}',
        '{"mode":1}',
        '{"mode":"SCHEDULED"}',
        '{"mode":" scheduled "}',
    ),
)
def test_malformed_or_unsupported_payload_is_rejected_before_invocation(payload):
    client = FakeLambda([])

    with pytest.raises(ValueError):
        invoke_retry.invoke_with_retry(
            client=client,
            function_name="trainer",
            payload=payload,
            sleep=lambda _seconds: pytest.fail("invalid payload must not sleep"),
        )

    assert client.calls == []


def test_success_preserves_exact_request_bytes_and_closes_stream():
    success, stream = _response()
    client = FakeLambda([success])
    payload = '{"sport":"mlb","mode":"scheduled","run":"exact"}'

    body, metadata = invoke_retry.invoke_with_retry(
        client=client,
        function_name="trainer-arn",
        payload=payload,
    )

    assert body == b'{"ok":true}'
    assert stream.closed is True
    assert client.calls == [
        {
            "FunctionName": "trainer-arn",
            "InvocationType": "RequestResponse",
            "Payload": payload.encode("utf-8"),
        }
    ]
    assert metadata["StatusCode"] == 200
    assert metadata["ExecutedVersion"] == "$LATEST"
    assert "ResponseMetadata" not in metadata
    assert "FunctionError" not in metadata


def test_main_cleans_stale_evidence_and_atomically_writes_only_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")
    invoke_retry._evidence_temporary(response_path).write_text(
        "stale temporary response", encoding="utf-8"
    )
    invoke_retry._evidence_temporary(invocation_path).write_text(
        "stale temporary invocation", encoding="utf-8"
    )
    success, _stream = _response()
    client = FakeLambda([success])
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
    metadata = json.loads(invocation_path.read_text(encoding="utf-8"))
    assert metadata["StatusCode"] == 200
    assert metadata["InvocationRetryControl"]["retryEnabled"] is True
    assert metadata["InvocationRetryControl"][
        "preAdmissionCapacityRetryEnabled"
    ] is True
    assert metadata["InvocationRetryControl"]["executionLeaseRetryEnabled"] is False
    assert metadata["InvocationRetryControl"]["invocationAttempts"] == 1
    assert not invoke_retry._evidence_temporary(response_path).exists()
    assert not invoke_retry._evidence_temporary(invocation_path).exists()
    assert client_config["service_name"] == "lambda"
    assert client_config["region_name"] == "us-east-1"
    assert client_config["config"].connect_timeout == 10
    assert client_config["config"].read_timeout == 1000
    assert client_config["config"].retries["total_max_attempts"] == 1


def test_main_leaves_no_stale_or_partial_evidence_after_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")

    def terminal_failure(**_kwargs):
        raise invoke_retry.TrainerInvocationFunctionError("terminal")

    monkeypatch.setattr(invoke_retry, "invoke_with_retry", terminal_failure)
    monkeypatch.setattr(invoke_retry.boto3, "client", lambda *_args, **_kwargs: object())
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
            '{"mode":"scheduled"}',
            "--response",
            str(response_path),
            "--invocation",
            str(invocation_path),
        ],
    )

    with pytest.raises(invoke_retry.TrainerInvocationFunctionError):
        invoke_retry.main()

    assert not response_path.exists()
    assert not invocation_path.exists()
    assert not invoke_retry._evidence_temporary(response_path).exists()
    assert not invoke_retry._evidence_temporary(invocation_path).exists()


def test_pair_persistence_cleans_both_outputs_if_second_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    original_replace = Path.replace

    def fail_invocation_replace(self, target):
        if Path(target) == invocation_path:
            raise OSError("injected second replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_invocation_replace)

    with pytest.raises(OSError, match="second replace"):
        invoke_retry._persist_canonical_evidence(
            response_path=response_path,
            response_body=b'{"ok":true}',
            invocation_path=invocation_path,
            invocation_metadata={"StatusCode": 200},
        )

    assert not response_path.exists()
    assert not invocation_path.exists()
    assert not invoke_retry._evidence_temporary(response_path).exists()
    assert not invoke_retry._evidence_temporary(invocation_path).exists()


def test_pair_persistence_rejects_overlapping_evidence_paths(tmp_path: Path):
    path = tmp_path / "same.json"

    with pytest.raises(ValueError, match="must be distinct"):
        invoke_retry._persist_canonical_evidence(
            response_path=path,
            response_body=b'{"ok":true}',
            invocation_path=path,
            invocation_metadata={"StatusCode": 200},
        )

    assert not path.exists()


def test_pair_persistence_rejects_noncanonical_evidence_and_cleans_stale_files(
    tmp_path: Path,
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")

    with pytest.raises(invoke_retry.TrainerInvocationResponseError):
        invoke_retry._persist_canonical_evidence(
            response_path=response_path,
            response_body=b'{"ok":false}',
            invocation_path=invocation_path,
            invocation_metadata={"StatusCode": 200},
        )

    assert not response_path.exists()
    assert not invocation_path.exists()
    assert not invoke_retry._evidence_temporary(response_path).exists()
    assert not invoke_retry._evidence_temporary(invocation_path).exists()


def test_main_rejects_overlapping_evidence_paths_before_creating_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "same.json"
    monkeypatch.setattr(
        invoke_retry.boto3,
        "client",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid evidence paths must fail before creating the AWS client"
        ),
    )
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
            '{"mode":"scheduled"}',
            "--response",
            str(path),
            "--invocation",
            str(path),
        ],
    )

    with pytest.raises(ValueError, match="must be distinct"):
        invoke_retry.main()

    assert not path.exists()


def test_main_rejects_bad_payload_before_creating_client_and_cleans_stale_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    response_path = tmp_path / "response.json"
    invocation_path = tmp_path / "invocation.json"
    response_path.write_text("stale response", encoding="utf-8")
    invocation_path.write_text("stale invocation", encoding="utf-8")
    monkeypatch.setattr(
        invoke_retry.boto3,
        "client",
        lambda *_args, **_kwargs: pytest.fail(
            "invalid payload must fail before creating the AWS client"
        ),
    )
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
            '{"mode":"SCHEDULED"}',
            "--response",
            str(response_path),
            "--invocation",
            str(invocation_path),
        ],
    )

    with pytest.raises(ValueError, match="mode is unsupported"):
        invoke_retry.main()

    assert not response_path.exists()
    assert not invocation_path.exists()


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
    client = FakeLambda([_response()[0]])
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
