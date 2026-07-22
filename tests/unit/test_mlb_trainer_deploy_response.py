from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from scripts import verify_mlb_trainer_deploy_response as verifier


HELLO_WORLD = Path(__file__).resolve().parents[2] / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_aws_training_v1 as aws_training


GIT_SHA = "a" * 40
TEMPLATE_SHA = "b" * 64
STARTED = "2026-07-22T12:00:00+00:00"


def _sign_status(payload):
    payload["statusFingerprintVersion"] = verifier.STATUS_FINGERPRINT_VERSION
    payload["statusFingerprint"] = verifier._status_fingerprint(payload)
    return payload


def test_deploy_lease_contract_exactly_matches_runtime_attestation() -> None:
    assert verifier.EXECUTION_CONCURRENCY_CONTROL == (
        aws_training.execution_concurrency_control(acquired_for_run=True)
    )


def test_deploy_fingerprint_contract_exactly_matches_runtime_attestation() -> None:
    assert (
        verifier.STATUS_FINGERPRINT_VERSION
        == aws_training.STATUS_FINGERPRINT_VERSION
    )


def _payloads():
    identity = {"gitSha": GIT_SHA, "templateSha256": TEMPLATE_SHA}
    common = {
        "version": verifier.TRAINER_VERSION,
        "experimentId": verifier.EXPERIMENT_ID,
        "releaseCutoffUtc": verifier.RELEASE_CUTOFF_UTC,
        "deploymentIdentity": identity,
    }
    manifest = {
        "version": verifier.EXPERIMENT_VERSION,
        "experimentId": verifier.EXPERIMENT_ID,
        "releaseContractId": verifier.EXPERIMENT_ID,
        "releaseCutoffUtc": verifier.RELEASE_CUTOFF_UTC,
        "createdAtUtc": "2026-07-21T23:00:00+00:00",
        "manifestDigest": "manifest-digest",
        "phase": "ACCUMULATING_TRAIN",
    }
    training = {
        **common,
        "executionConcurrencyControl": copy.deepcopy(
            verifier.EXECUTION_CONCURRENCY_CONTROL
        ),
        "ok": True,
        "status": "ACCUMULATING_TRAIN",
        "executionMode": "training",
        "runId": "training-run-1",
        "createdAtUtc": "2026-07-22T12:00:01+00:00",
        "championChanged": False,
        "automaticPromotionEnabled": False,
        "liveInferenceAuthority": False,
        "milestones": {"stage": "TRAIN_0_OF_300"},
        "statusFingerprintVersion": verifier.STATUS_FINGERPRINT_VERSION,
    }
    selection = {
        **common,
        "executionConcurrencyControl": copy.deepcopy(
            verifier.EXECUTION_CONCURRENCY_CONTROL
        ),
        "ok": True,
        "status": "WAITING_FOR_PERSISTED_CHALLENGER",
        "executionMode": "selection_capture",
        "runId": "selection-run-1",
        "createdAtUtc": "2026-07-22T12:00:02+00:00",
        "historicalTrainingScanInvoked": False,
        "modelTrained": False,
        "liveInferenceAuthority": False,
        "statusFingerprintVersion": verifier.STATUS_FINGERPRINT_VERSION,
    }
    _sign_status(training)
    _sign_status(selection)
    after = {
        **common,
        "ok": True,
        "manifest": manifest,
        "champion": None,
        "automaticPromotionEnabled": False,
        "firstPromotionRequiresManualReview": True,
        "v2InferenceConsumerInstalled": False,
        "runtimeAuthorityActivationAvailable": False,
        "trainingHealth": {
            "ok": True,
            "executionMode": "training",
            "deploymentIdentityMatches": True,
            "errors": [],
            "latestRun": copy.deepcopy(training),
        },
        "selectionCaptureHealth": {
            "ok": True,
            "executionMode": "selection_capture",
            "deploymentIdentityMatches": True,
            "errors": [],
            "latestRun": copy.deepcopy(selection),
        },
        "requestedRunEvidence": {
            "training": {
                "ok": True,
                "found": True,
                "requestedRunId": training["runId"],
                "executionMode": "training",
                "run": copy.deepcopy(training),
                "deploymentIdentityMatches": True,
                "errors": [],
            },
            "selectionCapture": {
                "ok": True,
                "found": True,
                "requestedRunId": selection["runId"],
                "executionMode": "selection_capture",
                "run": copy.deepcopy(selection),
                "deploymentIdentityMatches": True,
                "errors": [],
            },
        },
    }
    return training, selection, after


def _verify(training, selection, after, invocation_metadata=None):
    return verifier.verify(
        training=training,
        selection_capture=selection,
        status_after=after,
        invocation_metadata=invocation_metadata
        or tuple({"StatusCode": 200} for _ in range(3)),
        run_started_at=STARTED,
        expected_git_sha=GIT_SHA,
        expected_template_sha256=TEMPLATE_SHA,
    )


def test_accepts_fresh_split_run_and_status_health() -> None:
    assert _verify(*_payloads()) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            lambda training, selection, after: training.update(
                {"createdAtUtc": "2026-07-22T11:59:59+00:00"}
            ),
            "training_run_not_fresh_for_deploy",
        ),
        (
            lambda training, selection, after: training.update(
                {"championChanged": True}
            ),
            "training_run_does_not_prove_champion_unchanged",
        ),
        (
            lambda training, selection, after: selection.update(
                {"liveInferenceAuthority": True}
            ),
            "selection_capture_enabled_live_inference_authority",
        ),
        (
            lambda training, selection, after: after.update(
                {"automaticPromotionEnabled": True}
            ),
            "automatic_promotion_not_disabled",
        ),
        (
            lambda training, selection, after: after.update(
                {"firstPromotionRequiresManualReview": False}
            ),
            "manual_first_promotion_not_required",
        ),
        (
            lambda training, selection, after: training.update(
                {"version": "stale-trainer"}
            ),
            "training_trainer_version_mismatch",
        ),
        (
            lambda training, selection, after: selection.update(
                {"releaseCutoffUtc": "2026-07-21T19:30:00+00:00"}
            ),
            "selection_capture_release_cutoff_mismatch",
        ),
        (
            lambda training, selection, after: after["manifest"].update(
                {"releaseContractId": "stale-release"}
            ),
            "manifest_release_contract_identity_mismatch",
        ),
        (
            lambda training, selection, after: after["manifest"].update(
                {"createdAtUtc": verifier.RELEASE_CUTOFF_UTC}
            ),
            "manifest_not_created_before_release_cutoff",
        ),
        (
            lambda training, selection, after: after.update(
                {
                    "deploymentIdentity": {
                        "gitSha": "wrong",
                        "templateSha256": TEMPLATE_SHA,
                    }
                }
            ),
            "status_after_git_identity_mismatch",
        ),
        (
            lambda training, selection, after: training.pop("milestones"),
            "training_run_milestones_missing",
        ),
        (
            lambda training, selection, after: training.pop(
                "executionConcurrencyControl"
            ),
            "training_execution_lease_evidence_missing",
        ),
        (
            lambda training, selection, after: selection[
                "executionConcurrencyControl"
            ].update({"acquiredForRun": False}),
            "selection_capture_execution_lease_acquiredForRun_mismatch",
        ),
        (
            lambda training, selection, after: training[
                "executionConcurrencyControl"
            ].update({"protectedExecutionModes": ["training"]}),
            "training_execution_lease_protectedExecutionModes_mismatch",
        ),
        (
            lambda training, selection, after: after[
                "selectionCaptureHealth"
            ].update({"ok": False}),
            "selection_capture_health_not_ok",
        ),
        (
            lambda training, selection, after: after["trainingHealth"][
                "latestRun"
            ].update({"status": "STALE"}),
            "training_latest_run_status_fingerprint_mismatch",
        ),
        (
            lambda training, selection, after: training.update(
                {"statusFingerprintVersion": "stale-fingerprint-contract"}
            ),
            "training_status_fingerprint_version_mismatch",
        ),
        (
            lambda training, selection, after: after["selectionCaptureHealth"][
                "latestRun"
            ].update({"statusFingerprintVersion": "stale-fingerprint-contract"}),
            "selection_capture_latest_run_status_fingerprint_version_mismatch",
        ),
    ),
)
def test_rejects_unsafe_or_stale_deploy_evidence(mutation, expected) -> None:
    training, selection, after = copy.deepcopy(_payloads())
    mutation(training, selection, after)

    assert expected in _verify(training, selection, after)


def test_rejects_lambda_function_error() -> None:
    payloads = _payloads()
    invocation_metadata = tuple({"StatusCode": 200} for _ in range(3))
    invocation_metadata[1]["FunctionError"] = "Unhandled"

    assert "lambda_invocation_failed:1" in _verify(
        *payloads,
        invocation_metadata=invocation_metadata,
    )


def test_function_error_diagnostic_allowlists_exact_safe_fields() -> None:
    diagnostic = verifier.invocation_failure_diagnostic(
        label="training",
        response={
            "errorType": "ValidationException",
            "errorMessage": "Invalid lease condition",
            "requestId": "request-123",
            "stackTrace": ["must-not-appear"],
            "unknown": "must-not-appear",
        },
        invocation={"StatusCode": 200, "FunctionError": "Unhandled"},
    )

    assert diagnostic == {
        "label": "training",
        "functionError": "Unhandled",
        "errorType": "ValidationException",
        "errorMessage": "Invalid lease condition",
        "requestId": "request-123",
    }
    assert "stackTrace" not in diagnostic
    assert "unknown" not in diagnostic


def test_function_error_diagnostic_redacts_secrets_and_log_injection() -> None:
    diagnostic = verifier.invocation_failure_diagnostic(
        label="selection_capture\nforged",
        response={
            "errorType": "RuntimeError",
            "errorMessage": (
                "x-api-key: bbs_live_secret "
                "Authorization: Bearer bearer-credential "
                "authorization=Basic basic-credential "
                "apiKey=query-secret "
                "AWS_SECRET_ACCESS_KEY=aws-secret "
                "sessionToken=session-secret "
                "ghp_abcdefghijklmnopqrstuvwxyz1234567890 "
                "github_pat_abcdefghijklmnopqrstuvwxyz1234567890 "
                "AKIAABCDEFGHIJKLMNOP "
                "arn:aws:lambda:us-east-1:123456789012:function:name\r\nforged"
            ),
            "requestId": "123456789012",
        },
        invocation={"StatusCode": 200, "FunctionError": "Unhandled"},
    )

    rendered = json.dumps(diagnostic, sort_keys=True)
    for secret in (
        "bbs_live_secret",
        "bearer-credential",
        "basic-credential",
        "query-secret",
        "aws-secret",
        "session-secret",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "github_pat_abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIAABCDEFGHIJKLMNOP",
        "123456789012",
        "arn:aws",
    ):
        assert secret not in rendered
    assert "\n" not in diagnostic["label"]
    assert "\n" not in diagnostic["errorMessage"]
    assert "\r" not in diagnostic["errorMessage"]


def test_function_error_diagnostic_truncates_long_messages() -> None:
    diagnostic = verifier.invocation_failure_diagnostic(
        label="training",
        response={"errorMessage": "x" * 5000},
        invocation={"FunctionError": "Unhandled"},
    )

    assert diagnostic["errorMessage"].endswith("...[truncated]")
    assert len(diagnostic["errorMessage"]) < 1700


def test_successful_invocation_emits_no_failure_diagnostic() -> None:
    assert (
        verifier.invocation_failure_diagnostic(
            label="training",
            response={"ok": True},
            invocation={"StatusCode": 200},
        )
        is None
    )


def test_exact_run_evidence_survives_a_newer_latest_status_race() -> None:
    training, selection, after = _payloads()
    newer_training = copy.deepcopy(training)
    newer_training.update(
        {
            "runId": "training-run-2",
            "createdAtUtc": "2026-07-22T12:00:05+00:00",
            "status": "NEWER_SCHEDULED_RUN",
        }
    )
    newer_training.pop("statusFingerprint", None)
    _sign_status(newer_training)
    after["trainingHealth"]["latestRun"] = newer_training

    assert _verify(training, selection, after) == []


def test_rejects_requested_run_substitution_even_if_latest_is_healthy() -> None:
    training, selection, after = _payloads()
    exact = after["requestedRunEvidence"]["training"]["run"]
    exact["runId"] = "substituted-run"
    exact.pop("statusFingerprint", None)
    _sign_status(exact)

    errors = _verify(training, selection, after)

    assert "training_requested_run_does_not_match_deploy_run" in errors
    assert "training_requested_run_record_id_mismatch" in errors


def test_verifier_status_fingerprint_matches_runtime_canonical_numbers() -> None:
    payload = {
        "runId": "roundtrip-run",
        "nested": {
            "integralFloat": 90.0,
            "integralDecimal": aws_training.Decimal("90.00"),
            "negativeZero": -0.0,
        },
    }

    assert verifier._status_fingerprint(payload) == aws_training._status_fingerprint(
        payload
    )
