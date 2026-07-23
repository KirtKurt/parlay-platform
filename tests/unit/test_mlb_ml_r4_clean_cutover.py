from __future__ import annotations

from datetime import datetime, timezone

import pytest

import mlb_ml_aws_training_v1 as training
import mlb_ml_experiment_v2 as experiment


def _config(**overrides):
    values = {
        "artifacts_bucket": "versioned-artifacts",
        "experiment_id": experiment.PRODUCTION_EXPERIMENT_ID,
        "release_contract_id": experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        "release_cutoff_utc": experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
        "feature_vector_version": "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v2-lock-safe-temporal-missingness",
        "deployment_git_sha": "a" * 40,
        "deployment_template_sha256": "b" * 64,
        "automatic_promotion_enabled": False,
    }
    values.update(overrides)
    return training.TrainingConfig(**values)


def test_r4_cutoff_is_clean_future_boundary_after_unrecoverable_r3_slate():
    assert experiment.PRODUCTION_EXPERIMENT_ID == (
        "mlb-v2-2026-07-24-future-prospective-r4"
    )
    assert (
        experiment.PRODUCTION_RELEASE_CONTRACT_ID
        == experiment.PRODUCTION_EXPERIMENT_ID
    )
    assert (
        experiment.PRODUCTION_RELEASE_CUTOFF_UTC
        == "2026-07-24T04:00:00+00:00"
    )

    activation = experiment.release_activation(
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
        activated_at_utc="2026-07-23T13:00:00+00:00",
        deployment_git_sha="a" * 40,
        deployment_template_sha256="b" * 64,
    )
    assert activation["immutable"] is True
    assert activation["activatedAtUtc"] < activation["releaseCutoffUtc"]


def test_r4_loader_does_not_request_or_backfill_july_22(monkeypatch):
    monkeypatch.setenv("OUTCOMES_TABLE", "outcomes")
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    calls = []

    def forbidden_schedule(date):
        calls.append(("schedule", date))
        raise AssertionError("no pre-r4 slate may be requested")

    def forbidden_finalization(date, official):
        calls.append(("finalization", date))
        raise AssertionError("no pre-r4 slate may be finalized")

    rows = training.load_canonical_training_rows(
        _config(),
        now=datetime(2026, 7, 23, 13, 30, tzinfo=timezone.utc),
        official_schedule_loader=forbidden_schedule,
        slate_finalization_loader=forbidden_finalization,
    )

    assert list(rows) == []
    assert calls == []
    assert rows.continuity["ok"] is True
    assert rows.continuity["processedSlateDates"] == []
    assert rows.continuity["blockedSlateDate"] is None
    assert rows.continuity["finalizedGameSlateDates"] == []


def test_r3_identity_is_rejected_by_r4_production_config():
    with pytest.raises(training.TrainingContractError, match="r4 experiment ID"):
        _config(experiment_id="mlb-v2-2026-07-22-future-prospective-r3")
