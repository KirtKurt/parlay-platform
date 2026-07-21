from __future__ import annotations

import copy

import pytest

from scripts import verify_mlb_trainer_deploy_response as verifier


GIT_SHA = "a" * 40
TEMPLATE_SHA = "b" * 64
STARTED = "2026-07-22T12:00:00+00:00"


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
        "createdAtUtc": "2026-07-22T12:00:01+00:00",
        "championChanged": False,
        "automaticPromotionEnabled": False,
        "liveInferenceAuthority": False,
        "milestones": {"stage": "TRAIN_0_OF_300"},
    }
    selection = {
        **common,
        "executionConcurrencyControl": copy.deepcopy(
            verifier.EXECUTION_CONCURRENCY_CONTROL
        ),
        "ok": True,
        "status": "WAITING_FOR_PERSISTED_CHALLENGER",
        "executionMode": "selection_capture",
        "createdAtUtc": "2026-07-22T12:00:02+00:00",
        "historicalTrainingScanInvoked": False,
        "modelTrained": False,
        "liveInferenceAuthority": False,
    }
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
            "latestRun": copy.deepcopy(training),
        },
        "selectionCaptureHealth": {
            "ok": True,
            "executionMode": "selection_capture",
            "deploymentIdentityMatches": True,
            "latestRun": copy.deepcopy(selection),
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
            "training_latest_run_does_not_match_deploy_run",
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
