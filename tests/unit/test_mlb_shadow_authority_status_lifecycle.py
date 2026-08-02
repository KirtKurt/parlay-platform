from __future__ import annotations

from scripts import verify_mlb_trainer_deploy_response as verifier


def _manual_first_shadow_status(*, activation_available: bool) -> dict:
    return {
        "automaticPromotionEnabled": False,
        "firstPromotionRequiresManualReview": True,
        "v2InferenceConsumerInstalled": False,
        "liveInferenceAuthority": False,
        "champion": None,
        "runtimeAuthorityActivationAvailable": activation_available,
    }


def test_manual_first_shadow_deploy_accepts_unavailable_runtime_activation() -> None:
    status = _manual_first_shadow_status(activation_available=False)

    assert verifier._runtime_authority_activation_errors(status) == []


def test_manual_first_shadow_deploy_rejects_premature_runtime_activation() -> None:
    status = _manual_first_shadow_status(activation_available=True)

    assert verifier._runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_must_remain_unavailable_before_manual_approval"
    ]


def test_live_authority_path_still_requires_runtime_activation() -> None:
    status = {
        "automaticPromotionEnabled": True,
        "firstPromotionRequiresManualReview": False,
        "v2InferenceConsumerInstalled": True,
        "liveInferenceAuthority": True,
        "champion": {"modelId": "fixture-champion"},
        "runtimeAuthorityActivationAvailable": True,
    }
    assert verifier._runtime_authority_activation_errors(status) == []

    status["runtimeAuthorityActivationAvailable"] = False
    assert verifier._runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_not_available"
    ]
