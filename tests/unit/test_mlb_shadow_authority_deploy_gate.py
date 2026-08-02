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


def test_persisted_shadow_champion_does_not_enable_runtime_activation():
    assert _runtime_authority_activation_errors(
        _status(champion={"artifactDigest": "shadow-only-not-manually-approved"})
    ) == []


def test_persisted_shadow_champion_rejects_premature_runtime_activation():
    assert _runtime_authority_activation_errors(
        _status(
            champion={"artifactDigest": "shadow-only-not-manually-approved"},
            runtimeAuthorityActivationAvailable=True,
        )
    ) == [
        "runtime_authority_activation_must_remain_unavailable_before_manual_approval"
    ]


def test_authoritative_state_requires_runtime_activation_path():
    status = _status(
        liveInferenceAuthority=True,
        v2InferenceConsumerInstalled=True,
        runtimeAuthorityActivationAvailable=False,
    )
    assert _runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_not_available"
    ]

    status["runtimeAuthorityActivationAvailable"] = True
    assert _runtime_authority_activation_errors(status) == []
