from __future__ import annotations

from types import SimpleNamespace

import mlb_ml_aws_training_v1_compat as compat


class _Store:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = []

    def load_status_run(self, experiment_id, run_id):
        self.calls.append((experiment_id, run_id))
        if self.error is not None:
            raise self.error
        return self.value


def _service(store):
    return SimpleNamespace(
        config=SimpleNamespace(experiment_id="mlb-test-experiment"),
        store=store,
    )


def _payload(run_id="run-1"):
    return {
        "ok": True,
        "runId": run_id,
        "status": "WAITING_FOR_CANONICAL_SLATE_CONTINUITY",
        "executionMode": "training",
        "modelTrained": False,
        "championChanged": False,
        "liveInferenceAuthority": False,
        "productionAuthorityChanged": False,
        "sampleCount": 1.0,
    }


def test_matching_persisted_run_is_returned_exactly():
    invoked = _payload()
    persisted = {**_payload(), "sampleCount": 1, "persistedMarker": True}
    store = _Store(persisted)

    result = compat.persisted_run_response(_service(store), invoked)

    assert result == persisted
    assert store.calls == [("mlb-test-experiment", "run-1")]


def test_different_persisted_run_id_is_rejected():
    invoked = _payload("run-1")
    store = _Store(_payload("run-2"))

    result = compat.persisted_run_response(_service(store), invoked)

    assert result == invoked
    assert result["runId"] == "run-1"


def test_missing_or_failed_readback_preserves_fail_closed_invocation_result():
    invoked = _payload()
    assert compat.persisted_run_response(_service(_Store(None)), invoked) == invoked
    assert (
        compat.persisted_run_response(
            _service(_Store(error=RuntimeError("temporary read failure"))),
            invoked,
        )
        == invoked
    )


def test_continuity_block_is_still_normalized_without_persisted_readback():
    blocked = {
        "ok": False,
        "status": "CANONICAL_SLATE_CONTINUITY_BLOCKED",
        "executionMode": "training",
        "modelTrained": False,
        "championChanged": False,
        "liveInferenceAuthority": False,
        "productionAuthorityChanged": False,
        "milestones": {"canonicalContinuityReady": False},
        "canonicalSlateContinuity": {"ok": False},
    }
    result = compat.persisted_run_response(SimpleNamespace(), blocked)
    assert result["ok"] is True
    assert result["status"] == "WAITING_FOR_CANONICAL_SLATE_CONTINUITY"
    assert result["modelTrained"] is False
    assert result["liveInferenceAuthority"] is False
