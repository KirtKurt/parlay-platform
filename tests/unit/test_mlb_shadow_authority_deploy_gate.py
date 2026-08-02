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


def _persisted_status_without_top_level_authority(**overrides):
    value = _status(**overrides)
    value.pop("liveInferenceAuthority", None)
    value.update(
        {
            "trainingHealth": {
                "ok": True,
                "latestRun": {"ok": True, "liveInferenceAuthority": False},
            },
            "selectionCaptureHealth": {
                "ok": True,
                "latestRun": {"ok": True, "liveInferenceAuthority": False},
            },
        }
    )
    return value


def test_manual_first_shadow_state_accepts_unavailable_runtime_activation():
    assert _runtime_authority_activation_errors(_status()) == []


def test_persisted_status_shape_accepts_two_independent_shadow_proofs():
    assert (
        _runtime_authority_activation_errors(
            _persisted_status_without_top_level_authority()
        )
        == []
    )


def test_missing_top_level_authority_without_health_proofs_fails_closed():
    status = _status()
    status.pop("liveInferenceAuthority")
    assert _runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_not_available"
    ]


def test_persisted_status_shape_fails_closed_when_one_shadow_proof_is_unsafe():
    status = _persisted_status_without_top_level_authority()
    status["selectionCaptureHealth"]["latestRun"]["liveInferenceAuthority"] = True
    assert _runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_not_available"
    ]


def test_persisted_status_shape_fails_closed_when_health_is_not_ok():
    status = _persisted_status_without_top_level_authority()
    status["trainingHealth"]["ok"] = False
    assert _runtime_authority_activation_errors(status) == [
        "runtime_authority_activation_not_available"
    ]


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
