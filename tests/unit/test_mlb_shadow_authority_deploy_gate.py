from scripts.verify_mlb_trainer_deploy_response import (
    _runtime_authority_activation_errors,
)


def _status(**overrides):
    value = {
        "automaticPromotionEnabled": False,
        "firstPromotionRequiresManualReview": True,
        "v2InferenceConsumerInstalled": False,
        "liveInferenceAuthority": False,
        "champion": None,
        "runtimeAuthorityActivationAvailable": False,
    }
    value.update(overrides)
    return value


def test_manual_first_shadow_state_accepts_unavailable_runtime_activation():
    assert _runtime_authority_activation_errors(_status()) == []


def test_manual_first_shadow_state_rejects_premature_activation_availability():
    assert _runtime_authority_activation_errors(
        _status(runtimeAuthorityActivationAvailable=True)
    ) == [
        "runtime_authority_activation_must_remain_unavailable_before_manual_approval"
    ]


def test_promoted_or_authoritative_state_requires_runtime_activation_path():
    assert _runtime_authority_activation_errors(
        _status(
            champion={"artifactDigest": "approved"},
            runtimeAuthorityActivationAvailable=False,
        )
    ) == ["runtime_authority_activation_not_available"]
