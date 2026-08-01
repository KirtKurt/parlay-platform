from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import mlb_ml_aws_training_v1 as trainer


EXPECTED_VERSION = "MLB-TRAINER-CANONICAL-CONTINUITY-WAIT-v1"


def _blocked_payload():
    return {
        "ok": False,
        "status": "CANONICAL_SLATE_CONTINUITY_BLOCKED",
        "executionMode": "training",
        "trainingReady": False,
        "modelTrained": False,
        "championChanged": False,
        "liveInferenceAuthority": False,
        "automaticPromotionEnabled": False,
        "milestones": {"canonicalContinuityReady": False},
        "canonicalSlateContinuity": {
            "ok": False,
            "blockedSlateDate": "2026-07-31",
            "blocker": "canonical_label_finalization_incomplete",
            "finalizedGameSlateDates": ["2026-07-30"],
        },
    }


def test_public_import_preserves_original_module_with_narrow_patch():
    assert Path(trainer.__file__).name == "mlb_ml_aws_training_v1.py"
    assert trainer.MLB_TRAINER_CONTINUITY_WAIT_COMPAT_VERSION == EXPECTED_VERSION
    assert Path(trainer.MLB_TRAINER_CANONICAL_IMPLEMENTATION_PATH).name == (
        "mlb_ml_aws_training_v1.py"
    )
    patched = trainer.TrainingService._save_run_status
    assert getattr(patched, "_mlb_continuity_wait_patch", False) is True
    assert getattr(patched, "_mlb_continuity_wait_version", None) == EXPECTED_VERSION


def test_exact_continuity_block_becomes_healthy_fail_closed_wait_without_mutation():
    payload = _blocked_payload()
    original = copy.deepcopy(payload)

    result = trainer._normalize_canonical_continuity_wait(payload)

    assert payload == original
    assert result is not payload
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["trainingReady"] is False
    assert result["waiting"] is True
    assert result["waitReason"] == "canonical_slate_continuity"
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["liveInferenceAuthority"] is False
    assert result["automaticPromotionEnabled"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["continuityWaitCompatibilityVersion"] == EXPECTED_VERSION
    assert result["canonicalSlateContinuity"] == original["canonicalSlateContinuity"]


def test_unexpected_failure_and_incomplete_evidence_remain_unhealthy():
    unexpected = {
        "ok": False,
        "status": "TRAINING_CONTRACT_FAILED",
        "executionMode": "training",
        "modelTrained": False,
        "championChanged": False,
    }
    incomplete = _blocked_payload()
    incomplete.pop("canonicalSlateContinuity")

    assert trainer._normalize_canonical_continuity_wait(unexpected) == unexpected
    assert trainer._normalize_canonical_continuity_wait(incomplete) == incomplete
    assert trainer._normalize_canonical_continuity_wait(unexpected)["ok"] is False
    assert trainer._normalize_canonical_continuity_wait(incomplete)["ok"] is False


class _Store:
    def __init__(self):
        self.saved = []
        self.released = []

    def load_manifest(self, _experiment_id):
        return {"manifestDigest": "manifest-digest"}

    def save_status(self, experiment_id, value):
        self.saved.append((experiment_id, copy.deepcopy(value)))
        return {"created": True}

    def acquire_execution_lease(self, experiment_id, **kwargs):
        return {"experimentId": experiment_id, **kwargs}

    def release_execution_lease(self, experiment_id, **kwargs):
        self.released.append({"experimentId": experiment_id, **kwargs})


def _service_with_blocked_run():
    service = object.__new__(trainer.TrainingService)
    service.config = SimpleNamespace(
        experiment_id="continuity-wait-test",
        release_cutoff_utc="2026-07-22T00:00:00+00:00",
        deployment_git_sha="test-sha",
        deployment_template_sha256="test-template-sha",
        automatic_promotion_enabled=False,
    )
    service.store = _Store()
    service.now = lambda: datetime(2026, 8, 1, 21, 30, tzinfo=timezone.utc)
    service._selection_capture_before_training = None
    service._execution_lease_acquired_for_run = False

    def attest(_lease, **_kwargs):
        service._execution_lease_acquired_for_run = True

    service.attest_execution_lease_acquired = attest
    service.run_scheduled = lambda: service._save_run_status(_blocked_payload())
    return service


def test_normalization_occurs_before_immutable_status_persistence():
    service = _service_with_blocked_run()

    result = service.run_scheduled()

    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert len(service.store.saved) == 1
    _experiment_id, saved = service.store.saved[0]
    assert saved["ok"] is True
    assert saved["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert saved["canonicalSlateContinuity"]["ok"] is False
    assert saved["statusFingerprint"]
    assert saved["productionAuthorityChanged"] is False


def test_lambda_returns_wait_without_function_error_or_authority_change(monkeypatch):
    service = _service_with_blocked_run()
    monkeypatch.setattr(trainer, "_service", lambda: service)
    monkeypatch.setenv(
        "MLB_ML_EXECUTION_LEASE_SECONDS", str(trainer.EXECUTION_LEASE_SECONDS)
    )
    context = SimpleNamespace(
        aws_request_id="continuity-wait-request",
        get_remaining_time_in_millis=lambda: 800_000,
    )

    result = trainer.lambda_handler({"mode": "scheduled"}, context)

    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["trainingReady"] is False
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["liveInferenceAuthority"] is False
    assert result["automaticPromotionEnabled"] is False
    assert result["productionAuthorityChanged"] is False
    assert len(service.store.saved) == 1
    assert len(service.store.released) == 1
