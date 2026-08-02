from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import mlb_ml_aws_training_v1_compat as compat


def _blocked():
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
        },
    }


def test_exact_continuity_block_becomes_healthy_fail_closed_wait():
    payload = _blocked()
    original = copy.deepcopy(payload)
    result = compat.normalize_canonical_continuity_wait(payload)
    assert payload == original
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["trainingReady"] is False
    assert result["waiting"] is True
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["liveInferenceAuthority"] is False
    assert result["automaticPromotionEnabled"] is False
    assert result["productionAuthorityChanged"] is False
    assert result["canonicalSlateContinuity"] == original["canonicalSlateContinuity"]


def test_unexpected_failure_remains_unhealthy():
    value = {
        "ok": False,
        "status": "TRAINING_CONTRACT_FAILED",
        "executionMode": "training",
        "modelTrained": False,
        "championChanged": False,
    }
    assert compat.normalize_canonical_continuity_wait(value) == value
    assert compat.normalize_canonical_continuity_wait(value)["ok"] is False


def test_training_return_boundary_normalizes_exact_wait(monkeypatch):
    trainer = compat.canonical
    service = object.__new__(trainer.TrainingService)
    monkeypatch.setattr(compat, "_original_run_scheduled", lambda _self: _blocked())
    result = trainer.TrainingService.run_scheduled(service)
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["trainingReady"] is False
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["liveInferenceAuthority"] is False
    assert result["productionAuthorityChanged"] is False


def test_training_return_boundary_preserves_unexpected_failure(monkeypatch):
    trainer = compat.canonical
    service = object.__new__(trainer.TrainingService)
    unhealthy = {
        "ok": False,
        "status": "TRAINING_CONTRACT_FAILED",
        "executionMode": "training",
        "modelTrained": False,
        "championChanged": False,
    }
    monkeypatch.setattr(compat, "_original_run_scheduled", lambda _self: unhealthy)
    result = trainer.TrainingService.run_scheduled(service)
    assert result == unhealthy
    assert result["ok"] is False


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


def _service():
    trainer = compat.canonical
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
    service.run_scheduled = lambda: service._save_run_status(_blocked())
    return service


def test_normalization_occurs_before_immutable_persistence():
    service = _service()
    result = service.run_scheduled()
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert len(service.store.saved) == 1
    saved = service.store.saved[0][1]
    assert saved["ok"] is True
    assert saved["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert saved["statusFingerprint"]
    assert saved["productionAuthorityChanged"] is False


def test_unique_lambda_handler_returns_wait_without_function_error(monkeypatch):
    service = _service()
    trainer = compat.canonical
    monkeypatch.setattr(trainer, "_service", lambda: service)
    monkeypatch.setenv(
        "MLB_ML_EXECUTION_LEASE_SECONDS", str(trainer.EXECUTION_LEASE_SECONDS)
    )
    context = SimpleNamespace(
        aws_request_id="continuity-wait-request",
        get_remaining_time_in_millis=lambda: 800_000,
    )
    result = compat.lambda_handler({"mode": "scheduled"}, context)
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert result["productionAuthorityChanged"] is False
    assert len(service.store.released) == 1
