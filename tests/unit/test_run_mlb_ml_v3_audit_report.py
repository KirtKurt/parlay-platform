from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts import run_mlb_ml_v3_audit_report as audit_report
import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment


NOW = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)
EXPERIMENT_ID = experiment.PRODUCTION_EXPERIMENT_ID
PK = f"MLB_ML_EXPERIMENT#V2#{EXPERIMENT_ID}"
DEPLOYMENT = {"gitSha": "a" * 40, "templateSha256": "b" * 64}
MANIFEST = experiment.new_manifest(
    experiment_id=EXPERIMENT_ID,
    release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
    release_cutoff_utc="2026-07-22T04:00:00+00:00",
    feature_vector_version="vector-v2",
    model_feature_schemas={
        "outcome": list(trainer.dual_model.OUTCOME_FEATURES),
        "reliability": list(trainer.dual_model.RELIABILITY_FEATURES),
    },
    created_at_utc="2026-07-21T00:00:00+00:00",
)


class FakeTable:
    def __init__(self, records: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.records = records

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        assert kwargs["ConsistentRead"] is True
        data = self.records.get((key["PK"], key["SK"]))
        return {"Item": {"data": data}} if data is not None else {}


class FakeDynamoResource:
    def __init__(self, table: FakeTable) -> None:
        self.table = table

    def Table(self, name: str) -> FakeTable:
        assert name == "snapshots"
        return self.table


class AdvancingManifestTable(FakeTable):
    def __init__(
        self,
        records: dict[tuple[str, str], dict[str, Any]],
        manifest_before: dict[str, Any],
        manifest_after: dict[str, Any],
    ) -> None:
        super().__init__(records)
        self.manifests = [manifest_before, manifest_after]
        self.manifest_reads = 0

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        key = kwargs["Key"]
        if (key["PK"], key["SK"]) == (PK, "MANIFEST"):
            assert kwargs["ConsistentRead"] is True
            index = min(self.manifest_reads, len(self.manifests) - 1)
            self.manifest_reads += 1
            return {"Item": {"data": self.manifests[index]}}
        return super().get_item(**kwargs)


def _install_table(
    monkeypatch,
    records: dict[tuple[str, str], dict[str, Any]],
    *,
    include_manifest: bool = True,
) -> None:
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    records = dict(records)
    if include_manifest:
        records.setdefault((PK, "MANIFEST"), MANIFEST)
    resource = FakeDynamoResource(FakeTable(records))
    monkeypatch.setattr("boto3.resource", lambda service: resource)
    monkeypatch.setattr(
        audit_report, "_read_deployed_trainer_identity", lambda: DEPLOYMENT
    )


def _install_advancing_manifest_table(
    monkeypatch,
    records: dict[tuple[str, str], dict[str, Any]],
    manifest_before: dict[str, Any],
    manifest_after: dict[str, Any],
) -> None:
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    table = AdvancingManifestTable(records, manifest_before, manifest_after)
    resource = FakeDynamoResource(table)
    monkeypatch.setattr("boto3.resource", lambda service: resource)
    monkeypatch.setattr(
        audit_report, "_read_deployed_trainer_identity", lambda: DEPLOYMENT
    )


def test_v2_status_freshness_and_automatic_promotion_come_from_latest_status(
    monkeypatch,
) -> None:
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status(
                "training", 15, automatic_promotion=True
            ),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 10
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["statusPresent"] is True
    assert result["latestRunTimestampValid"] is True
    assert result["latestRunAgeMinutes"] == 15.0
    assert result["latestRunFresh"] is True
    assert result["latestRunMaxAgeMinutes"] == 480.0
    assert result["selectionCaptureHealth"]["latestRunFresh"] is True
    assert result["deploymentIdentityAgreement"] is True
    assert result["automaticPromotionEnabled"] is True


def test_missing_or_stale_v2_status_is_not_reported_healthy(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status("training", 15),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 46
            ),
        },
    )

    stale = audit_report._read_v2_training_state(now_utc=NOW)
    assert stale["ok"] is False
    assert stale["statusPresent"] is True
    assert stale["latestRunFresh"] is True
    assert stale["selectionCaptureHealth"]["latestRunFresh"] is False

    _install_table(monkeypatch, {}, include_manifest=False)
    missing = audit_report._read_v2_training_state(now_utc=NOW)
    assert missing["ok"] is False
    assert missing["statusPresent"] is False
    assert missing["latestRunTimestampValid"] is False
    assert missing["latestRunAgeMinutes"] is None
    assert missing["latestRunFresh"] is False
    assert missing["selectionCaptureHealth"]["statusPresent"] is False
    assert missing["automaticPromotionEnabled"] is None


def _status(
    mode: str,
    age_minutes: int,
    *,
    ok: bool = True,
    git_sha: str = "a" * 40,
    automatic_promotion: bool = False,
    manifest_digest: str | None = None,
) -> dict[str, Any]:
    value = {
        "ok": ok,
        "status": f"{mode.upper()}_STATUS",
        "executionMode": mode,
        "createdAtUtc": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "automaticPromotionEnabled": automatic_promotion,
        "milestones": {"source": mode},
        "version": trainer.VERSION,
        "experimentId": EXPERIMENT_ID,
        "manifestDigest": manifest_digest or MANIFEST["manifestDigest"],
        "deploymentIdentity": {
            "gitSha": git_sha,
            "templateSha256": "b" * 64,
        },
        "statusFingerprintVersion": trainer.STATUS_FINGERPRINT_VERSION,
        "executionConcurrencyControl": trainer.execution_concurrency_control(
            acquired_for_run=True
        ),
    }
    value["statusFingerprint"] = trainer._status_fingerprint(value)
    return value


def test_generic_latest_never_overrides_mode_specific_authority(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-21-future-prospective-r2"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    generic = _status("selection_capture", 1, ok=False)
    generic["automaticPromotionEnabled"] = True
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): generic,
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 300),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 15
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["latestRunAgeMinutes"] == 300.0
    assert result["latestRunMaxAgeMinutes"] == 480.0
    assert result["milestones"] == {"source": "training"}
    assert result["automaticPromotionEnabled"] is False
    assert result["genericLatestStatusDiagnosticOnly"] == generic


def test_fresh_capture_cannot_mask_stale_or_failed_training(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-21-future-prospective-r2"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): _status("selection_capture", 1),
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 481),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )

    stale = audit_report._read_v2_training_state(now_utc=NOW)

    assert stale["ok"] is False
    assert stale["trainingHealth"]["ok"] is False
    assert stale["selectionCaptureHealth"]["ok"] is True

    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST"): _status("selection_capture", 1),
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 1, ok=False),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )
    failed = audit_report._read_v2_training_state(now_utc=NOW)
    assert failed["ok"] is False
    assert "status_not_ok" in failed["trainingHealth"]["errors"]


def test_matching_fresh_modes_require_same_deployment_identity(monkeypatch) -> None:
    experiment_id = "mlb-v2-2026-07-21-future-prospective-r2"
    pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    _install_table(
        monkeypatch,
        {
            (pk, "STATUS#LATEST#TRAINING"): _status("training", 1),
            (pk, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1, git_sha="c" * 40
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["trainingHealth"]["ok"] is True
    assert result["selectionCaptureHealth"]["ok"] is False
    assert "status_deployment_identity_mismatch" in result[
        "selectionCaptureHealth"
    ]["errors"]
    assert result["deploymentIdentityAgreement"] is False
    assert result["ok"] is False


def test_status_contract_tamper_and_manifest_advance_fail_closed(monkeypatch) -> None:
    tampered = _status("training", 1)
    tampered["version"] = "stale-version"
    old_capture = _status("selection_capture", 1, manifest_digest="c" * 64)
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): tampered,
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): old_capture,
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert "status_version_mismatch" in result["trainingHealth"]["errors"]
    assert "status_fingerprint_mismatch" in result["trainingHealth"]["errors"]
    assert "status_manifest_mismatch" in result["selectionCaptureHealth"]["errors"]


def test_status_without_acquired_shared_lease_fails_audit_even_with_valid_fingerprint(
    monkeypatch,
) -> None:
    training = _status("training", 1)
    training["executionConcurrencyControl"] = trainer.execution_concurrency_control(
        acquired_for_run=False
    )
    training["statusFingerprint"] = trainer._status_fingerprint(training)
    _install_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): training,
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert "status_execution_lease_contract_mismatch" in result[
        "trainingHealth"
    ]["errors"]


def test_manifest_advance_during_status_reads_fails_closed(monkeypatch) -> None:
    advanced_manifest = copy.deepcopy(MANIFEST)
    advanced_manifest["revision"] = MANIFEST["revision"] + 1
    advanced_manifest["manifestDigest"] = experiment.manifest_digest(
        advanced_manifest
    )
    _install_advancing_manifest_table(
        monkeypatch,
        {
            (PK, "STATUS#LATEST#TRAINING"): _status("training", 1),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture", 1
            ),
        },
        MANIFEST,
        advanced_manifest,
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is False
    assert result["manifestReadStable"] is False
    assert result["manifestReadBefore"] == {
        "revision": MANIFEST["revision"],
        "manifestDigest": MANIFEST["manifestDigest"],
    }
    assert result["manifestReadAfter"] == {
        "revision": advanced_manifest["revision"],
        "manifestDigest": advanced_manifest["manifestDigest"],
    }
    assert "manifest_changed_during_status_read" in result["trainingHealth"][
        "errors"
    ]
    assert "manifest_changed_during_status_read" in result[
        "selectionCaptureHealth"
    ]["errors"]


def test_stable_manifest_and_recaptured_statuses_pass(monkeypatch) -> None:
    advanced_manifest = copy.deepcopy(MANIFEST)
    advanced_manifest["revision"] = MANIFEST["revision"] + 1
    advanced_manifest["manifestDigest"] = experiment.manifest_digest(
        advanced_manifest
    )
    _install_table(
        monkeypatch,
        {
            (PK, "MANIFEST"): advanced_manifest,
            (PK, "STATUS#LATEST#TRAINING"): _status(
                "training",
                1,
                manifest_digest=advanced_manifest["manifestDigest"],
            ),
            (PK, "STATUS#LATEST#SELECTION_CAPTURE"): _status(
                "selection_capture",
                1,
                manifest_digest=advanced_manifest["manifestDigest"],
            ),
        },
    )

    result = audit_report._read_v2_training_state(now_utc=NOW)

    assert result["ok"] is True
    assert result["manifestReadStable"] is True
    assert result["manifestReadBefore"] == result["manifestReadAfter"]


def test_deployed_trainer_identity_rejects_non_hex_attestation(monkeypatch) -> None:
    class CloudFormation:
        def describe_stack_resource(self, **kwargs):
            return {"StackResourceDetail": {"PhysicalResourceId": "trainer"}}

    class Lambda:
        def get_function_configuration(self, **kwargs):
            return {
                "Environment": {
                    "Variables": {
                        "INQSI_DEPLOY_GIT_SHA": "G" * 40,
                        "INQSI_DEPLOY_TEMPLATE_SHA256": "z" * 64,
                    }
                }
            }

    clients = {"cloudformation": CloudFormation(), "lambda": Lambda()}
    monkeypatch.setattr("boto3.client", lambda service, **kwargs: clients[service])

    with pytest.raises(RuntimeError, match="release identity is invalid"):
        audit_report._read_deployed_trainer_identity()
