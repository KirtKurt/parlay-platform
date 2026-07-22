import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_aws_training_v1 as aws_training
import mlb_ml_experiment_v2 as experiment


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class CapturedDynamoRequest(RuntimeError):
    pass


def frozen_challenger(manifest):
    return {
        "ok": True,
        "version": "frozen-test-challenger",
        "experimentId": manifest["experimentId"],
        "featureSchemaFingerprint": manifest["featureSchemaFingerprint"],
        "selectedThreshold": 0.6,
        "partitionProof": {
            "trainRowCount": manifest["partitions"]["train"].get("rowCount"),
            "validationRowCount": manifest["partitions"]["validation"].get(
                "rowCount"
            ),
            "prospectiveRowsUsedForFitOrThreshold": 0,
            "trainFingerprint": manifest["partitions"]["train"].get(
                "partitionFingerprint"
            ),
            "validationFingerprint": manifest["partitions"]["validation"].get(
                "partitionFingerprint"
            ),
        },
        "thresholdSelectionSource": "validation_only_before_prospective_cutover",
        "automaticPromotionEnabled": False,
        "liveInferenceAuthority": False,
    }


def config(auto=False):
    return aws_training.TrainingConfig(
        artifacts_bucket="versioned-artifacts",
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="vector-v2",
        deployment_git_sha="a" * 40,
        deployment_template_sha256="b" * 64,
        automatic_promotion_enabled=auto,
    )


def healthy_status(execution_mode, *, created_at=NOW, status="HEALTHY"):
    value = {
        "ok": True,
        "status": status,
        "executionMode": execution_mode,
        "version": aws_training.VERSION,
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "manifestDigest": None,
        "createdAtUtc": created_at.isoformat(),
        "deploymentIdentity": {
            "gitSha": "a" * 40,
            "templateSha256": "b" * 64,
        },
        "statusFingerprintVersion": aws_training.STATUS_FINGERPRINT_VERSION,
        "executionConcurrencyControl": aws_training.execution_concurrency_control(
            acquired_for_run=True
        ),
    }
    value["statusFingerprint"] = aws_training._status_fingerprint(value)
    return value


def test_status_fingerprint_survives_dynamodb_numeric_round_trip():
    status = healthy_status("training")
    status["milestones"] = {
        "assumedCleanGamesPerFullSlate": 15.0,
        "aspirationalAccuracyPct": 90.0,
        "maximumCalibrationError": 0.08,
    }
    status["statusFingerprint"] = aws_training._status_fingerprint(status)
    round_tripped = aws_training._plain(aws_training._ddb_safe(status))

    assert round_tripped["milestones"]["assumedCleanGamesPerFullSlate"] == 15
    assert round_tripped["milestones"]["aspirationalAccuracyPct"] == 90
    assert round_tripped["milestones"]["maximumCalibrationError"] == 0.08
    assert status["statusFingerprint"] == aws_training._status_fingerprint(
        round_tripped
    )


def new_manifest(sealed=False):
    value = experiment.new_manifest(
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="vector-v2",
        model_feature_schemas={
            "outcome": list(aws_training.dual_model.OUTCOME_FEATURES),
            "reliability": list(aws_training.dual_model.RELIABILITY_FEATURES),
        },
        created_at_utc="2026-07-21T00:00:00+00:00",
    )
    if sealed:
        for name, count in {
            "train": 300,
            "validation": 100,
            "prospectiveTest": 100,
        }.items():
            value["partitions"][name]["rowCount"] = count
            value["partitions"][name]["frozen"] = True
            value["partitions"][name]["partitionFingerprint"] = f"{name}-fp"
        value["phase"] = "PROSPECTIVE_TEST_SEALED_AWAITING_EVALUATION"
        value["prospectiveTestSealed"] = True
        value["validationEndSlateDate"] = "2026-08-20"
        value["prospectiveCutoverAtUtc"] = "2026-08-21T00:00:00+00:00"
        value["prospectiveAfterSlateDate"] = "2026-08-20"
        challenger = frozen_challenger(value)
        challenger_digest = aws_training._sha256(challenger)
        value["frozenChallenger"] = {
            "artifact": {
                "bucket": "versioned-artifacts",
                "key": "frozen/challenger.json",
                "versionId": "frozen-v1",
                "sha256": challenger_digest,
            },
            "artifactDigest": challenger_digest,
            "selectedThreshold": 0.6,
            "trainingPartitionFingerprint": "train-fp",
            "validationPartitionFingerprint": "validation-fp",
            "boundAtUtc": "2026-08-21T00:00:00+00:00",
            "automaticAuthority": False,
        }
        value["manifestDigest"] = experiment.manifest_digest(value)
    return value


def trained_bundle(manifest):
    return {
        "ok": True,
        "version": "dual-v2",
        "experimentId": manifest["experimentId"],
        "experimentManifestDigest": manifest["manifestDigest"],
        "featureSchemaFingerprint": manifest["featureSchemaFingerprint"],
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "split": {
            "counts": {
                "train": 300,
                "validation": 100,
                "prospectiveTest": 100,
            },
            "partitionFingerprints": {
                name: manifest["partitions"][name].get("partitionFingerprint")
                for name in experiment.PARTITION_ORDER
            },
        },
        "outcomeModel": {"ok": True, "weights": {"o0": 0.1}},
        "reliabilityModel": {
            "ok": True,
            "weights": {"r0": 0.2},
            "thresholdSelectedOnValidationOnly": True,
            "selectedThreshold": {
                "ok": True,
                "threshold": 0.6,
                "selectionSource": "validation_only",
            },
        },
        "validation": {"outcome": {}, "selectedReliability": {}},
        "prospectiveSelectedRecommendationCount": 100,
        "prospectiveTest": {
            "outcome": {
                "count": 100,
                "accuracyPct": 61.0,
                "accuracyLiftPctPoints": 2.0,
                "brierSkillPct": 4.0,
                "logLoss": 0.61,
                "calibrationError": 0.07,
                "baseline": {"logLoss": 0.65},
                "pairedAccuracyRegression": {
                    "ok": True,
                    "statisticallySignificantRegression": False,
                    "regressionPValue": 0.7,
                },
            },
            "selectedReliability": {
                "count": 100,
                "calibrationError": 0.07,
            },
        },
    }


class FakeStore:
    def __init__(self, manifest=None, fail_artifact_number=None):
        self.manifest = copy.deepcopy(manifest)
        self.fail_artifact_number = fail_artifact_number
        self.artifact_calls = 0
        self.artifacts = {}
        self.candidates = {}
        self.latest = None
        self.champion = None
        self.fail_next_save = False
        self.selections = []
        self.statuses = []
        self.latest_statuses = {}
        self.lease_owner = None
        self.lease_acquisitions = []
        self.lease_releases = []

    def load_manifest(self, experiment_id):
        return copy.deepcopy(self.manifest)

    def save_manifest(self, manifest, *, expected_revision, expected_digest):
        if self.fail_next_save:
            self.fail_next_save = False
            raise aws_training.ConditionalStateConflict("simulated conflict")
        if expected_revision is None:
            if self.manifest is not None:
                raise aws_training.ConditionalStateConflict("already exists")
        elif (
            self.manifest is None
            or self.manifest["revision"] != expected_revision
            or self.manifest["manifestDigest"] != expected_digest
        ):
            raise aws_training.ConditionalStateConflict("stale manifest")
        self.manifest = copy.deepcopy(manifest)

    def put_versioned_json(self, key, payload):
        self.artifact_calls += 1
        if self.artifact_calls == self.fail_artifact_number:
            raise RuntimeError("simulated S3 failure")
        value = self.artifacts.get(key)
        if value is None:
            value = {
                "bucket": "versioned-artifacts",
                "key": key,
                "versionId": f"version-{len(self.artifacts) + 1}",
                "sha256": aws_training._sha256(payload),
                "byteLength": len(aws_training._json_bytes(payload)),
                "contentType": "application/json",
            }
            self.artifacts[key] = value
        return copy.deepcopy(value)

    def read_versioned_json(self, artifact):
        challenger = frozen_challenger(self.manifest)
        assert artifact["sha256"] == aws_training._sha256(challenger)
        return copy.deepcopy(challenger)

    def record_selection(self, entry):
        identity = entry["recordIdentity"]
        existing = next(
            (
                value
                for value in self.selections
                if value.get("recordIdentity") == identity
            ),
            None,
        )
        if existing is not None:
            if existing.get("idempotencyFingerprint") != entry.get(
                "idempotencyFingerprint"
            ):
                raise aws_training.ConditionalStateConflict(
                    "immutable prospective selection changed"
                )
            return {
                "ok": True,
                "created": False,
                "capturedAtUtc": existing.get("capturedAtUtc"),
            }
        self.selections.append(copy.deepcopy(entry))
        return {"ok": True, "created": True}

    def list_selections(self, experiment_id):
        return copy.deepcopy(self.selections)

    def save_status(self, experiment_id, status):
        saved = copy.deepcopy(status)
        self.statuses.append(saved)
        self.latest_statuses[None] = saved
        mode = str(saved.get("executionMode") or "").strip().lower()
        if mode:
            self.latest_statuses[mode] = saved

    def load_latest_status(self, experiment_id, execution_mode=None):
        return copy.deepcopy(self.latest_statuses.get(execution_mode))

    def acquire_execution_lease(
        self,
        experiment_id,
        *,
        owner_token,
        execution_mode,
        acquired_at,
        lease_seconds,
    ):
        if self.lease_owner is not None:
            raise aws_training.ExecutionLeaseUnavailable("active lease")
        self.lease_owner = owner_token
        self.lease_acquisitions.append(
            {
                "experimentId": experiment_id,
                "ownerToken": owner_token,
                "executionMode": execution_mode,
                "acquiredAt": acquired_at,
                "leaseSeconds": lease_seconds,
            }
        )
        expires = acquired_at + aws_training.timedelta(seconds=lease_seconds)
        return {
            "PK": aws_training.EXECUTION_LEASE_PK,
            "SK": aws_training.EXECUTION_LEASE_SK,
            "record_type": aws_training.EXECUTION_LEASE_RECORD_TYPE,
            "experiment_id": experiment_id,
            "lease_owner": owner_token,
            "execution_mode": execution_mode,
            "acquired_at": acquired_at.isoformat(),
            "lease_expires_at": expires.isoformat(),
            "lease_expires_at_epoch": int(expires.timestamp()),
        }

    def release_execution_lease(self, experiment_id, *, owner_token):
        if self.lease_owner != owner_token:
            raise aws_training.ConditionalStateConflict("stale lease owner")
        self.lease_owner = None
        self.lease_releases.append(
            {"experimentId": experiment_id, "ownerToken": owner_token}
        )

    def commit_candidate(
        self,
        manifest,
        candidate,
        *,
        expected_revision,
        expected_digest,
    ):
        if (
            self.manifest["revision"] != expected_revision
            or self.manifest["manifestDigest"] != expected_digest
        ):
            raise aws_training.ConditionalStateConflict("stale transaction")
        if candidate["artifactDigest"] in self.candidates:
            raise aws_training.ConditionalStateConflict("duplicate candidate")
        self.manifest = copy.deepcopy(manifest)
        self.candidates[candidate["artifactDigest"]] = copy.deepcopy(candidate)
        self.latest = copy.deepcopy(candidate)

    def load_candidate(self, experiment_id, artifact_digest):
        return copy.deepcopy(self.candidates.get(artifact_digest))

    def load_latest_candidate(self, experiment_id):
        return copy.deepcopy(self.latest)

    def load_champion(self):
        return copy.deepcopy(self.champion)

    def promote_candidate(
        self,
        candidate,
        *,
        authorities,
        approval_mode,
        reviewer,
        stable_champion,
        expected_champion_digest,
    ):
        actual = (self.champion or {}).get("artifactDigest")
        if actual != expected_champion_digest:
            raise aws_training.ConditionalStateConflict("stale champion")
        self.champion = {
            "artifactDigest": candidate["artifactDigest"],
            "experimentId": candidate["experimentId"],
            "directionApproved": "direction" in authorities,
            "playabilityApproved": "playability" in authorities,
            "stableChampionApproved": stable_champion,
            "directionAuthorityEnabled": False,
            "playabilityAuthorityEnabled": False,
            "stableChampion": False,
            "shadowOnly": True,
            "runtimeIntegrationRequired": True,
            "runtimeAuthorityActivated": False,
            "approvalMode": approval_mode,
            "reviewer": reviewer,
            "deploymentIdentity": copy.deepcopy(
                candidate.get("deploymentIdentity") or {}
            ),
        }
        return copy.deepcopy(self.champion)


def attest_test_execution_lease(training, execution_mode="training"):
    owner_token = f"test:{execution_mode}"
    expires = NOW + aws_training.timedelta(
        seconds=aws_training.EXECUTION_LEASE_SECONDS
    )
    training.attest_execution_lease_acquired(
        {
            "PK": aws_training.EXECUTION_LEASE_PK,
            "SK": aws_training.EXECUTION_LEASE_SK,
            "record_type": aws_training.EXECUTION_LEASE_RECORD_TYPE,
            "experiment_id": experiment.PRODUCTION_EXPERIMENT_ID,
            "lease_owner": owner_token,
            "execution_mode": execution_mode,
            "lease_expires_at_epoch": int(expires.timestamp()),
        },
        owner_token=owner_token,
        execution_mode=execution_mode,
    )
    return training


def service(store, auto=False, lease_mode="training"):
    training = aws_training.TrainingService(
        store,
        config(auto=auto),
        row_loader=lambda _config: [],
        now=lambda: NOW,
    )
    return (
        attest_test_execution_lease(training, lease_mode)
        if lease_mode is not None
        else training
    )


def patch_sealed_training(monkeypatch):
    monkeypatch.setattr(
        experiment,
        "filter_records",
        lambda rows, manifest: {
            "acceptedRows": [],
            "acceptedRowCount": 0,
            "rejectedRows": [],
            "rejectedRowCount": 0,
            "rejectionReasonCounts": {},
        },
    )
    monkeypatch.setattr(
        experiment,
        "rows_by_partition",
        lambda manifest, rows: {
            "train": [],
            "validation": [],
            "prospectiveTest": [],
        },
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "evaluate_frozen_challenger",
        lambda rows, manifest, challenger: trained_bundle(manifest),
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "evaluate_selection_ledger",
        lambda rows, entries, challenger_artifact_digest, experiment_manifest: {
            "ok": True,
            "settledSelectedRecommendationCount": 100,
            "metrics": {
                "count": 100,
                "calibrationError": 0.07,
            },
            "conflicts": [],
        },
    )
    monkeypatch.setattr(
        aws_training.TrainingService,
        "_capture_selections",
        lambda self, manifest, challenger: {
            "ok": True,
            "capturedCount": 0,
            "selectedCount": 0,
        },
    )


def test_accumulation_registers_no_model_or_champion():
    store = FakeStore()
    result = service(store).run()
    assert result["ok"] is True
    assert result["modelTrained"] is False
    assert result["championChanged"] is False
    assert store.candidates == {}
    assert store.champion is None
    assert store.artifact_calls == 0


@pytest.mark.parametrize(
    "operation",
    ("training", "selection_capture", "manual_review"),
)
def test_direct_mutating_service_entrypoints_require_attested_lease(operation):
    store = FakeStore()
    training = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: [],
        now=lambda: NOW,
    )

    with pytest.raises(
        aws_training.ExecutionLeaseRequired,
        match="active attested execution lease is required",
    ):
        if operation == "training":
            training.run()
        elif operation == "selection_capture":
            training.capture_selections()
        else:
            training.manual_review(
                artifact_digest="candidate",
                reviewer="reviewer@example.com",
                requested_authorities=["direction"],
                stable_champion=False,
            )

    assert store.manifest is None
    assert store.selections == []
    assert store.statuses == []
    assert store.candidates == {}
    assert store.champion is None


@pytest.mark.parametrize(
    ("operation", "lease_mode"),
    (("training", "selection_capture"), ("manual_review", "training")),
)
def test_mutating_service_entrypoints_reject_the_wrong_lease_mode(
    operation, lease_mode
):
    store = FakeStore()
    training = service(store, lease_mode=lease_mode)

    with pytest.raises(
        aws_training.ExecutionLeaseRequired,
        match="active attested execution lease is required",
    ):
        if operation == "training":
            training.run()
        else:
            training.manual_review(
                artifact_digest="candidate",
                reviewer="reviewer@example.com",
                requested_authorities=["direction"],
                stable_champion=False,
            )

    assert store.manifest is None
    assert store.statuses == []
    assert store.champion is None


def test_r2_cutoff_rejects_july20_and_every_pre_boundary_lock():
    manifest_value = service(FakeStore())._new_manifest()
    assert manifest_value["releaseCutoffUtc"] == "2026-07-22T04:00:00+00:00"
    row = {
        "gameId": "mlb_statsapi:cutoff-proof",
        "slateDateEt": "2026-07-20",
        "trainingEligible": True,
        "predictionPersistedAtUtc": "2026-07-21T19:29:59+00:00",
        "featureSnapshot": {
            "version": "vector-v2",
            "fingerprint": "cutoff-proof-fingerprint",
            "lockAtUtc": "2026-07-21T19:29:59+00:00",
        },
    }

    _, july20_reasons = experiment.validate_record(row, manifest_value)
    assert "pre_release_or_missing_lock_timestamp" in july20_reasons

    at_boundary = copy.deepcopy(row)
    at_boundary["slateDateEt"] = "2026-07-21"
    at_boundary["featureSnapshot"]["lockAtUtc"] = (
        "2026-07-22T04:00:00+00:00"
    )
    _, boundary_reasons = experiment.validate_record(at_boundary, manifest_value)
    assert "pre_release_or_missing_lock_timestamp" not in boundary_reasons


def test_persisted_feature_schema_change_requires_a_new_experiment_id():
    old_schema_manifest = experiment.new_manifest(
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="vector-v2",
        model_feature_schemas={
            "outcome": [f"old_o{i}" for i in range(8)],
            "reliability": [f"old_r{i}" for i in range(8)],
        },
    )
    store = FakeStore(old_schema_manifest)

    with pytest.raises(
        aws_training.TrainingContractError,
        match="modelFeatureSchemas; create a new experiment ID",
    ):
        service(store).run()

    assert store.manifest == old_schema_manifest
    assert store.artifact_calls == 0
    assert store.candidates == {}


def test_status_is_read_only_and_attests_deployment_and_manual_first_policy():
    store = FakeStore()
    for execution_mode in ("training", "selection_capture"):
        store.latest_statuses[execution_mode] = healthy_status(execution_mode)
    result = service(store).status()

    assert result["ok"] is True
    assert result["deploymentIdentity"] == {
        "gitSha": "a" * 40,
        "templateSha256": "b" * 64,
    }
    assert result["automaticPromotionEnabled"] is False
    assert result["firstPromotionRequiresManualReview"] is True
    assert result["manualReviewCreatesShadowApprovalOnly"] is True
    assert result["v2InferenceConsumerInstalled"] is False
    assert result["runtimeAuthorityActivationAvailable"] is False
    assert result["releaseCutoffUtc"] == "2026-07-22T04:00:00+00:00"
    assert result["trainingHealth"]["ok"] is True
    assert result["selectionCaptureHealth"]["ok"] is True
    assert store.manifest is None
    assert store.artifact_calls == 0


def test_status_never_lets_fresh_capture_mask_stale_training():
    store = FakeStore()
    store.latest_statuses["training"] = healthy_status(
        "training",
        status="OLD_TRAINING",
        created_at=(
            NOW - aws_training.TRAINING_STATUS_MAX_AGE - aws_training.timedelta(seconds=1)
        ),
    )
    store.latest_statuses["selection_capture"] = healthy_status(
        "selection_capture", status="FRESH_CAPTURE"
    )

    result = service(store).status()

    assert result["ok"] is False
    assert "latest_status_stale" in result["trainingHealth"]["errors"]
    assert result["selectionCaptureHealth"]["ok"] is True


def test_status_fails_closed_when_training_or_capture_heartbeat_is_missing():
    result = service(FakeStore()).status()

    assert result["ok"] is False
    assert result["trainingHealth"]["ok"] is False
    assert "latest_status_missing" in result["trainingHealth"]["errors"]
    assert result["selectionCaptureHealth"]["ok"] is False
    assert "latest_status_missing" in result["selectionCaptureHealth"]["errors"]


def test_status_rejects_a_fingerprint_valid_run_without_acquired_lease_evidence():
    store = FakeStore()
    training = healthy_status("training")
    training["executionConcurrencyControl"] = (
        aws_training.execution_concurrency_control(acquired_for_run=False)
    )
    training["statusFingerprint"] = aws_training._status_fingerprint(training)
    store.latest_statuses["training"] = training
    store.latest_statuses["selection_capture"] = healthy_status(
        "selection_capture"
    )

    result = service(store).status()

    assert result["ok"] is False
    assert "latest_status_execution_lease_contract_mismatch" in result[
        "trainingHealth"
    ]["errors"]
    assert result["selectionCaptureHealth"]["ok"] is True


def test_selection_capture_mode_records_a_healthy_waiting_heartbeat():
    store = FakeStore()

    result = service(store).capture_selections()

    assert result["ok"] is True
    assert result["executionMode"] == "selection_capture"
    assert result["status"] == "WAITING_FOR_EXPERIMENT_MANIFEST"
    assert result["historicalTrainingScanInvoked"] is False
    assert store.manifest is None
    assert store.latest_statuses["selection_capture"] == result


def test_repeated_selection_capture_is_idempotent_and_preserves_first_timestamp(
    monkeypatch,
):
    import mlb_canonical_final_labels_v1 as labels

    store = FakeStore(new_manifest(sealed=True))
    clock = [NOW]
    row = {
        "gameId": "mlb_statsapi:selection-1",
        "officialGamePk": "selection-1",
        "slateDateEt": "2026-09-01",
        "commenceTime": "2026-09-01T14:00:00+00:00",
        "featureSnapshot": {"fingerprint": "f" * 64},
        "canonicalLockAuthority": {"learningEligible": True},
    }
    monkeypatch.setattr(
        labels,
        "load_canonical_locked_rows_without_labels",
        lambda **kwargs: {"ok": True, "rows": [copy.deepcopy(row)]},
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "score_unlabeled_lock",
        lambda row, challenger: {
            "outcomeProbability": 0.62,
            "reliabilityProbability": 0.72,
        },
    )
    capture = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: (_ for _ in ()).throw(
            AssertionError("selection capture must not scan historical labels")
        ),
        now=lambda: clock[0],
    )
    attest_test_execution_lease(capture, "selection_capture")

    first = capture.capture_selections()
    first_timestamp = store.selections[0]["capturedAtUtc"]
    clock[0] = NOW + aws_training.timedelta(minutes=5)
    second = capture.capture_selections()

    assert first["selectionCapture"]["capturedCount"] == 1
    assert first["selectionCapture"]["existingCount"] == 0
    assert second["selectionCapture"]["capturedCount"] == 0
    assert second["selectionCapture"]["existingCount"] == 1
    assert len(store.selections) == 1
    assert store.selections[0]["capturedAtUtc"] == first_timestamp


def test_capture_uses_one_decision_timestamp_for_the_entire_invocation(monkeypatch):
    import mlb_canonical_final_labels_v1 as labels

    rows = [
        {
            "gameId": f"mlb_statsapi:same-time-{index}",
            "officialGamePk": f"same-time-{index}",
            "slateDateEt": "2026-09-01",
            "commenceTime": "2026-09-01T14:00:00+00:00",
            "featureSnapshot": {"fingerprint": str(index) * 64},
            "canonicalLockAuthority": {"learningEligible": True},
        }
        for index in (1, 2)
    ]
    monkeypatch.setattr(
        labels,
        "load_canonical_locked_rows_without_labels",
        lambda **kwargs: {"ok": True, "rows": copy.deepcopy(rows)},
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "score_unlabeled_lock",
        lambda row, challenger: {"reliabilityProbability": 0.72},
    )
    now_calls = []

    def advancing_clock():
        value = NOW + aws_training.timedelta(minutes=len(now_calls))
        now_calls.append(value)
        return value

    store = FakeStore(new_manifest(sealed=True))
    training = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: [],
        now=advancing_clock,
    )

    result = training._capture_selections(store.manifest, frozen_challenger(store.manifest))

    assert result["ok"] is True
    assert result["capturedCount"] == 2
    assert len(now_calls) == 1
    assert {entry["capturedAtUtc"] for entry in store.selections} == {
        NOW.isoformat()
    }


def test_capture_skips_only_pre_cutover_or_already_started_games(monkeypatch):
    import mlb_canonical_final_labels_v1 as labels

    rows = [
        {
            "gameId": "mlb_statsapi:pre-cutover",
            "slateDateEt": "2026-08-20",
            "commenceTime": "2026-08-20T23:00:00+00:00",
            "featureSnapshot": {"fingerprint": "a" * 64},
            "canonicalLockAuthority": {"learningEligible": True},
        },
        {
            "gameId": "mlb_statsapi:already-started",
            "slateDateEt": "2026-09-01",
            "commenceTime": "2026-09-01T11:59:00+00:00",
            "featureSnapshot": {"fingerprint": "b" * 64},
            "canonicalLockAuthority": {"learningEligible": True},
        },
    ]
    monkeypatch.setattr(
        labels,
        "load_canonical_locked_rows_without_labels",
        lambda **kwargs: {"ok": True, "rows": copy.deepcopy(rows)},
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "score_unlabeled_lock",
        lambda row, challenger: (_ for _ in ()).throw(
            AssertionError("out-of-window rows must not be scored")
        ),
    )
    store = FakeStore(new_manifest(sealed=True))

    result = service(store)._capture_selections(
        store.manifest, frozen_challenger(store.manifest)
    )

    assert result["ok"] is True
    assert result["capturedCount"] == 0
    assert result["skippedCount"] == 2
    assert result["skipReasonCounts"] == {
        "capture_not_before_commence": 1,
        "game_not_after_challenger_cutover": 1,
    }
    assert result["errors"] == []


def test_unexpected_eligible_selection_contract_failure_is_an_error(monkeypatch):
    import mlb_canonical_final_labels_v1 as labels

    malformed = {
        "gameId": "mlb_statsapi:missing-fingerprint",
        "slateDateEt": "2026-09-01",
        "commenceTime": "2026-09-01T14:00:00+00:00",
        "featureSnapshot": {},
        "canonicalLockAuthority": {"learningEligible": True},
    }
    monkeypatch.setattr(
        labels,
        "load_canonical_locked_rows_without_labels",
        lambda **kwargs: {"ok": True, "rows": [copy.deepcopy(malformed)]},
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "score_unlabeled_lock",
        lambda row, challenger: {"reliabilityProbability": 0.72},
    )
    store = FakeStore(new_manifest(sealed=True))

    result = service(store)._capture_selections(
        store.manifest, frozen_challenger(store.manifest)
    )

    assert result["ok"] is False
    assert result["capturedCount"] == 0
    assert result["skippedCount"] == 0
    assert result["errors"] == [
        {
            "gameId": "mlb_statsapi:missing-fingerprint",
            "type": "ExperimentContractError",
            "error": "immutable lock identity is required",
        }
    ]


def test_capture_rejects_tampered_challenger_binding_before_scoring():
    manifest = new_manifest(sealed=True)
    manifest["frozenChallenger"]["trainingPartitionFingerprint"] = "tampered"
    manifest["manifestDigest"] = experiment.manifest_digest(manifest)
    store = FakeStore(manifest)

    with pytest.raises(
        aws_training.TrainingContractError,
        match="persisted challenger manifest binding is invalid: bound_train_partition",
    ):
        service(store).capture_selections()

    assert store.selections == []


def test_sealed_experiment_fails_closed_on_invalid_selection_ledger(monkeypatch):
    patch_sealed_training(monkeypatch)
    monkeypatch.setattr(
        aws_training.dual_model,
        "evaluate_selection_ledger",
        lambda rows, entries, challenger_artifact_digest, experiment_manifest: {
            "ok": False,
            "settledSelectedRecommendationCount": 0,
            "metrics": {},
            "conflicts": [{"reason": "tampered_selection"}],
        },
    )
    store = FakeStore(new_manifest(sealed=True))

    result = service(store).run()

    assert result["ok"] is False
    assert result["status"] == "SELECTION_LEDGER_CONTRACT_INVALID"
    assert store.candidates == {}


def test_lambda_failure_records_unhealthy_mode_status_before_reraising(monkeypatch):
    store = FakeStore()
    failing = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: (_ for _ in ()).throw(
            RuntimeError("canonical label read failed")
        ),
        now=lambda: NOW,
    )
    monkeypatch.setattr(aws_training, "_service", lambda: failing)
    context = type("Context", (), {"aws_request_id": "request-123"})()

    with pytest.raises(RuntimeError, match="canonical label read failed"):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    latest = store.latest_statuses["training"]
    assert latest["ok"] is False
    assert latest["runId"] == "request-123"
    assert latest["status"] == "TRAINING_INVOCATION_FAILED"
    assert latest["failure"] == {
        "type": "RuntimeError",
        "message": "canonical label read failed",
    }
    assert latest["executionConcurrencyControl"]["acquiredForRun"] is True
    assert store.lease_owner is None
    assert len(store.lease_acquisitions) == 1
    assert len(store.lease_releases) == 1


@pytest.mark.parametrize(
    ("mode", "expected_execution_mode"),
    (("scheduled", "training"), ("selection_capture", "selection_capture")),
)
def test_lambda_mutating_modes_share_lease_and_release_after_success(
    monkeypatch, mode, expected_execution_mode
):
    store = FakeStore()
    training = service(store, lease_mode=None)
    monkeypatch.setattr(aws_training, "_service", lambda: training)
    context = type(
        "Context",
        (),
        {
            "aws_request_id": f"request-{mode}",
            "get_remaining_time_in_millis": lambda self: 900_000,
        },
    )()

    result = aws_training.lambda_handler({"mode": mode}, context)

    assert result["ok"] is True
    assert result["executionMode"] == expected_execution_mode
    assert result["executionConcurrencyControl"] == {
        "version": aws_training.EXECUTION_LEASE_VERSION,
        "strategy": "dynamodb_conditional_lease",
        "scope": "one_global_lease_across_experiments_and_modes",
        "leasePartitionKey": aws_training.EXECUTION_LEASE_PK,
        "migrationAnchorExperimentId": (
            aws_training.EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
        ),
        "leaseKey": aws_training.EXECUTION_LEASE_SK,
        "leaseSeconds": aws_training.EXECUTION_LEASE_SECONDS,
        "protectedExecutionModes": [
            "manual_review",
            "selection_capture",
            "training",
        ],
        "acquiredForRun": True,
        "expiredLeaseReclaimEnabled": True,
        "ownerConditionalRelease": True,
        "reservedLambdaConcurrencyRequired": False,
    }
    assert store.lease_acquisitions[0]["executionMode"] == expected_execution_mode
    assert store.lease_owner is None
    assert len(store.lease_releases) == 1


def test_scheduled_training_captures_before_a_colliding_capture_invocation(
    monkeypatch,
):
    import mlb_canonical_final_labels_v1 as labels

    store = FakeStore(new_manifest(sealed=True))
    row = {
        "gameId": "mlb_statsapi:training-collision",
        "officialGamePk": "training-collision",
        "slateDateEt": "2026-09-01",
        "commenceTime": "2026-09-01T14:00:00+00:00",
        "featureSnapshot": {"fingerprint": "c" * 64},
        "canonicalLockAuthority": {"learningEligible": True},
    }
    monkeypatch.setattr(
        labels,
        "load_canonical_locked_rows_without_labels",
        lambda **kwargs: {"ok": True, "rows": [copy.deepcopy(row)]},
    )
    monkeypatch.setattr(
        aws_training.dual_model,
        "score_unlabeled_lock",
        lambda row, challenger: {"reliabilityProbability": 0.72},
    )
    scheduled_service = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: [],
        now=lambda: NOW,
    )
    colliding_capture_service = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: [],
        now=lambda: NOW,
    )
    services = iter((scheduled_service, colliding_capture_service))
    monkeypatch.setattr(aws_training, "_service", lambda: next(services))
    collision_observed = []

    def training_after_capture():
        assert len(store.selections) == 1
        inner_context = type(
            "Context",
            (),
            {
                "aws_request_id": "colliding-capture",
                "get_remaining_time_in_millis": lambda self: 900_000,
            },
        )()
        with pytest.raises(aws_training.ExecutionLeaseUnavailable):
            aws_training.lambda_handler(
                {"mode": "selection_capture"}, inner_context
            )
        collision_observed.append(True)
        return scheduled_service._save_run_status(
            {
                "ok": True,
                "status": "TEST_TRAINING_COMPLETE",
                "executionMode": "training",
                "modelTrained": False,
                "championChanged": False,
            }
        )

    monkeypatch.setattr(scheduled_service, "run", training_after_capture)
    outer_context = type(
        "Context",
        (),
        {
            "aws_request_id": "scheduled-training",
            "get_remaining_time_in_millis": lambda self: 900_000,
        },
    )()

    result = aws_training.lambda_handler({"mode": "scheduled"}, outer_context)

    assert collision_observed == [True]
    assert len(store.selections) == 1
    assert result["selectionCaptureBeforeTraining"] == {
        "ok": True,
        "status": "PROSPECTIVE_SELECTION_CAPTURE_COMPLETE",
        "capturedCount": 1,
        "existingCount": 0,
        "selectedCount": 1,
        "skippedCount": 0,
        "collisionPolicy": (
            "capture_before_training_under_shared_execution_lease"
        ),
        "selectionWritesIdempotent": True,
    }
    assert result["statusFingerprint"] == aws_training._status_fingerprint(result)
    assert store.latest_statuses["training"] == result
    assert store.lease_owner is None
    assert len(store.lease_acquisitions) == 1
    assert len(store.lease_releases) == 1


@pytest.mark.parametrize("mode", ("scheduled", "selection_capture", "manual_review"))
def test_lambda_lease_contention_is_retryable_without_overwriting_health(
    monkeypatch, mode
):
    store = FakeStore()
    store.lease_owner = "training:active-owner"
    existing = healthy_status("training", status="ACTIVE_TRAINING_HEALTH")
    store.latest_statuses["training"] = copy.deepcopy(existing)
    training = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: (_ for _ in ()).throw(
            AssertionError("contending invocation must not execute")
        ),
        now=lambda: NOW,
    )
    monkeypatch.setattr(aws_training, "_service", lambda: training)
    context = type("Context", (), {"aws_request_id": "contender"})()

    with pytest.raises(aws_training.ExecutionLeaseUnavailable, match="active lease"):
        aws_training.lambda_handler({"mode": mode}, context)

    assert store.latest_statuses["training"] == existing
    assert store.statuses == []
    assert store.lease_owner == "training:active-owner"
    assert store.lease_releases == []


def test_ambiguous_lease_acquire_failure_makes_no_unlocked_status_write(
    monkeypatch,
):
    store = FakeStore()
    existing = healthy_status("training", status="PREVIOUS_HEALTH")
    store.latest_statuses["training"] = copy.deepcopy(existing)

    def ambiguous_failure(*args, **kwargs):
        raise RuntimeError("DynamoDB connection closed after request")

    store.acquire_execution_lease = ambiguous_failure
    monkeypatch.setattr(
        aws_training, "_service", lambda: service(store, lease_mode=None)
    )
    context = type("Context", (), {"aws_request_id": "ambiguous-request"})()

    with pytest.raises(RuntimeError, match="connection closed"):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    assert store.latest_statuses["training"] == existing
    assert store.statuses == []
    assert store.lease_releases == []


def test_ambiguous_successful_lease_write_makes_no_unlocked_state_write(
    monkeypatch,
):
    store = FakeStore()

    def response_lost_after_write(
        experiment_id,
        *,
        owner_token,
        execution_mode,
        acquired_at,
        lease_seconds,
    ):
        store.lease_owner = owner_token
        raise RuntimeError("DynamoDB response lost after conditional write")

    store.acquire_execution_lease = response_lost_after_write
    monkeypatch.setattr(
        aws_training, "_service", lambda: service(store, lease_mode=None)
    )
    context = type("Context", (), {"aws_request_id": "ambiguous-success"})()

    with pytest.raises(RuntimeError, match="response lost"):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    assert store.lease_owner == "training:ambiguous-success"
    assert store.manifest is None
    assert store.statuses == []
    assert store.artifact_calls == 0
    assert store.lease_releases == []


def test_release_failure_turns_success_into_retryable_invocation_failure(
    monkeypatch,
):
    store = FakeStore()

    def release_failed(*args, **kwargs):
        raise RuntimeError("lease release response lost")

    store.release_execution_lease = release_failed
    monkeypatch.setattr(
        aws_training, "_service", lambda: service(store, lease_mode=None)
    )
    context = type("Context", (), {"aws_request_id": "release-failure"})()

    with pytest.raises(RuntimeError, match="lease release response lost"):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    assert store.latest_statuses["training"]["ok"] is True
    assert store.latest_statuses["training"]["executionConcurrencyControl"][
        "acquiredForRun"
    ] is True
    assert store.lease_owner == "training:release-failure"


def test_release_failure_does_not_mask_primary_training_failure(monkeypatch):
    store = FakeStore()
    failing = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: (_ for _ in ()).throw(
            RuntimeError("primary canonical read failure")
        ),
        now=lambda: NOW,
    )

    def release_failed(*args, **kwargs):
        raise RuntimeError("secondary release failure")

    store.release_execution_lease = release_failed
    monkeypatch.setattr(aws_training, "_service", lambda: failing)
    context = type("Context", (), {"aws_request_id": "primary-failure"})()

    with pytest.raises(RuntimeError, match="primary canonical read failure"):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    assert store.latest_statuses["training"]["ok"] is False
    assert store.lease_owner == "training:primary-failure"


@pytest.mark.parametrize(
    ("configured_seconds", "remaining_milliseconds", "message"),
    (
        (900, 900_000, "configured execution lease duration"),
        (960, 901_000, "execution lease must outlive"),
    ),
)
def test_lambda_rejects_unsafe_lease_runtime_boundaries_before_acquire(
    monkeypatch, configured_seconds, remaining_milliseconds, message
):
    store = FakeStore()
    monkeypatch.setattr(
        aws_training, "_service", lambda: service(store, lease_mode=None)
    )
    monkeypatch.setenv(
        "MLB_ML_EXECUTION_LEASE_SECONDS", str(configured_seconds)
    )
    context = type(
        "Context",
        (),
        {
            "aws_request_id": "unsafe-boundary",
            "get_remaining_time_in_millis": (
                lambda self: remaining_milliseconds
            ),
        },
    )()

    with pytest.raises(aws_training.TrainingContractError, match=message):
        aws_training.lambda_handler({"mode": "scheduled"}, context)

    assert store.lease_acquisitions == []
    assert store.statuses == []


def test_artifact_failure_never_advances_candidate_or_champion(monkeypatch):
    patch_sealed_training(monkeypatch)
    store = FakeStore(new_manifest(sealed=True), fail_artifact_number=3)
    with pytest.raises(RuntimeError, match="simulated S3 failure"):
        service(store).run()
    assert store.candidates == {}
    assert store.latest is None
    assert store.champion is None


def test_candidate_and_evaluated_manifest_commit_together_and_are_idempotent(
    monkeypatch,
):
    patch_sealed_training(monkeypatch)
    store = FakeStore(new_manifest(sealed=True))
    first = service(store).run()
    assert first["status"] == "CANDIDATE_REGISTERED"
    assert first["promotionGate"]["promotionDecision"] == (
        "PENDING_MANUAL_FIRST_SHADOW_APPROVAL"
    )
    assert first["championChanged"] is False
    assert store.manifest["prospectiveTestEvaluated"] is True
    assert (
        store.manifest["prospectiveEvaluationFingerprint"]
        == first["evaluationFingerprint"]
    )
    assert len(store.artifacts) == 4
    assert store.latest["deploymentIdentity"] == {
        "gitSha": "a" * 40,
        "templateSha256": "b" * 64,
    }

    second = service(store).run()
    assert second["status"] == "CANDIDATE_REGISTERED"
    assert second["artifactDigest"] == first["artifactDigest"]
    assert len(store.candidates) == 1
    assert store.artifact_calls == 8


def test_manual_review_requires_exact_digest_and_only_eligible_authorities(
    monkeypatch,
):
    patch_sealed_training(monkeypatch)
    store = FakeStore(new_manifest(sealed=True))
    result = service(store).run()
    with pytest.raises(
        aws_training.TrainingContractError,
        match="reviewed candidate digest was not found",
    ):
        service(store, lease_mode="manual_review").manual_review(
            artifact_digest="wrong",
            reviewer="reviewer@example.com",
            requested_authorities=["direction"],
            stable_champion=True,
        )

    approved = service(store, lease_mode="manual_review").manual_review(
        artifact_digest=result["artifactDigest"],
        reviewer="reviewer@example.com",
        requested_authorities=["direction", "playability"],
        stable_champion=True,
    )
    assert approved["status"] == "MANUALLY_REVIEWED_SHADOW_CHAMPION_APPROVED"
    assert approved["approvedForFutureRuntimeIntegration"] == [
        "direction",
        "playability",
    ]
    assert approved["runtimeAuthorityActivated"] is False
    assert approved["runtimeIntegrationRequired"] is True
    assert approved["champion"]["directionApproved"] is True
    assert approved["champion"]["playabilityApproved"] is True
    assert approved["champion"]["directionAuthorityEnabled"] is False
    assert approved["champion"]["playabilityAuthorityEnabled"] is False
    assert approved["champion"]["stableChampionApproved"] is True
    assert approved["champion"]["stableChampion"] is False
    assert approved["champion"]["shadowOnly"] is True
    assert approved["champion"]["reviewer"] == "reviewer@example.com"


def test_lambda_manual_review_uses_the_same_execution_lease(monkeypatch):
    patch_sealed_training(monkeypatch)
    store = FakeStore(new_manifest(sealed=True))
    candidate = service(store).run()
    handler_service = service(store, lease_mode=None)
    monkeypatch.setattr(aws_training, "_service", lambda: handler_service)
    context = type(
        "Context",
        (),
        {
            "aws_request_id": "manual-review-request",
            "get_remaining_time_in_millis": lambda self: 900_000,
        },
    )()

    result = aws_training.lambda_handler(
        {
            "mode": "manual_review",
            "artifactDigest": candidate["artifactDigest"],
            "reviewer": "reviewer@example.com",
            "authorities": ["direction", "playability"],
            "stableChampion": True,
        },
        context,
    )

    assert result["status"] == "MANUALLY_REVIEWED_SHADOW_CHAMPION_APPROVED"
    assert result["executionConcurrencyControl"] == (
        aws_training.execution_concurrency_control(acquired_for_run=True)
    )
    assert store.lease_acquisitions[-1]["executionMode"] == "manual_review"
    assert store.lease_owner is None
    assert len(store.lease_releases) == 1


def test_auto_promotion_requires_preexisting_stable_champion(monkeypatch):
    patch_sealed_training(monkeypatch)
    store = FakeStore(new_manifest(sealed=True))
    store.champion = {
        "artifactDigest": "old-stable",
        "stableChampion": True,
        "directionAuthorityEnabled": True,
    }
    result = service(store, auto=True).run()
    assert result["promotionGate"]["promotionDecision"] == (
        "AUTO_SHADOW_APPROVAL_ELIGIBLE"
    )
    assert result["championChanged"] is True
    assert result["runtimeAuthorityChanged"] is False
    assert store.champion["artifactDigest"] == result["artifactDigest"]
    assert store.champion["directionAuthorityEnabled"] is False
    assert store.champion["playabilityAuthorityEnabled"] is False
    assert store.champion["runtimeIntegrationRequired"] is True


def test_conditional_manifest_conflict_stops_before_artifacts_or_pointer():
    store = FakeStore()
    store.fail_next_save = True
    with pytest.raises(aws_training.ConditionalStateConflict):
        service(store).run()
    assert store.artifact_calls == 0
    assert store.candidates == {}
    assert store.champion is None


def test_partial_prior_et_slate_cannot_freeze_or_deadlock_manifest(monkeypatch):
    import mlb_canonical_final_labels_v1 as canonical_labels

    monkeypatch.setenv("OUTCOMES_TABLE", "outcomes")
    monkeypatch.setenv("SNAPSHOTS_TABLE", "snapshots")
    cutoff_config = aws_training.TrainingConfig(
        artifacts_bucket="versioned-artifacts",
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
        feature_vector_version="vector-v2",
        deployment_git_sha="a" * 40,
        deployment_template_sha256="b" * 64,
    )
    after_midnight_et = datetime(2026, 7, 23, 4, 30, tzinfo=timezone.utc)
    row = {
        "gameId": "mlb_statsapi:partial-prior-date",
        "slateDateEt": "2026-07-22",
        "slateFinalized": False,
        "commenceTime": "2026-07-22T23:00:00+00:00",
        "featureSnapshot": {"fingerprint": "partial-fingerprint"},
    }
    state = {
        "report": {
            "ok": False,
            "requestedSlateDates": ["2026-07-22"],
            "finalizedSlateDates": [],
            # Defensive poison row: even if an upstream regression emitted it,
            # the AWS adapter must not admit a non-finalized slate.
            "rows": [copy.deepcopy(row)],
            "slates": [
                {
                    "slateDateEt": "2026-07-22",
                    "slateFinalized": False,
                    "officialGameCount": 15,
                    "officialFinalCount": 14,
                }
            ],
        }
    }
    requested = []

    def official_loader(slate_date):
        requested.append(slate_date)
        diagnostic = state["report"]["slates"][0]
        count = int(diagnostic["officialGameCount"])
        return {
            "ok": True,
            "source": canonical_labels.SOURCE,
            "sourceUrl": "https://statsapi.mlb.com/api/v1/schedule",
            "slateDateEt": slate_date,
            "officialGameCount": count,
            "officialFinalCount": int(diagnostic["officialFinalCount"]),
            "games": [
                {
                    "officialGamePk": str(index),
                    "officialDate": slate_date,
                    "completed": index <= int(diagnostic["officialFinalCount"]),
                }
                for index in range(1, count + 1)
            ],
        }

    def finalization_loader(slate_date, official):
        return copy.deepcopy(state["report"])
    monkeypatch.setattr(
        experiment,
        "filter_records",
        lambda rows, manifest: {
            "acceptedRows": list(rows),
            "acceptedRowCount": len(rows),
            "rejectedRows": [],
            "rejectedRowCount": 0,
            "rejectionReasonCounts": {},
        },
    )

    store = FakeStore()
    training = aws_training.TrainingService(
        store,
        cutoff_config,
        row_loader=lambda value: aws_training.load_canonical_training_rows(
            value,
            now=after_midnight_et,
            official_schedule_loader=official_loader,
            slate_finalization_loader=finalization_loader,
        ),
        now=lambda: after_midnight_et,
    )
    attest_test_execution_lease(training, "training")
    partial = training.run()

    assert requested == ["2026-07-22"]
    assert partial["status"] == "CANONICAL_SLATE_CONTINUITY_BLOCKED"
    assert partial["acceptedRowCount"] == 0
    assert store.manifest["assignedSlateDates"] == {}
    assert store.manifest["partitions"]["train"]["frozen"] is False

    row["slateFinalized"] = True
    state["report"] = {
        "ok": True,
        "requestedSlateDates": ["2026-07-22"],
        "finalizedSlateDates": ["2026-07-22"],
        "rows": [copy.deepcopy(row)],
        "slates": [
            {
                "slateDateEt": "2026-07-22",
                "slateFinalized": True,
                "officialGameCount": 15,
                "officialFinalCount": 15,
            }
        ],
    }
    completed = training.run()

    assert completed["acceptedRowCount"] == 1
    assert store.manifest["assignedSlateDates"]["2026-07-22"]["partition"] == "train"
    assert store.manifest["partitions"]["train"]["rowCount"] == 1
    assert store.manifest["partitions"]["train"]["frozen"] is False
    assert completed["milestones"]["firstFullCleanSlateProof"]["achieved"] is False
    slate_proof = completed["milestones"]["firstFullCleanSlateProof"][
        "evaluatedSlateProofs"
    ][0]
    assert slate_proof["officialGameCount"] == 15
    assert slate_proof["cleanEligibleGameCount"] == 0
    assert "official_game_set_missing_clean_rows" in slate_proof["errors"]
    authority = completed["canonicalSlateContinuity"][
        "finalizedSlateAuthorities"
    ]["2026-07-22"]
    assert authority["officialGameCount"] == 15
    assert experiment.official_finalized_slate_authority_errors(authority) == []


class FakeS3:
    def __init__(self, versioning="Enabled"):
        self.versioning = versioning
        self.objects = {}

    def get_bucket_versioning(self, **kwargs):
        return {"Status": self.versioning}

    def head_object(self, Bucket, Key, VersionId=None):
        if Key not in self.objects:
            raise KeyError(Key)
        return copy.deepcopy(self.objects[Key])

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        version = f"v-{len(self.objects) + 1}"
        self.objects[Key] = {
            "VersionId": version,
            "ContentLength": len(Body),
            "Metadata": Metadata,
        }
        return {"VersionId": version}


class FakeDdbResource:
    def Table(self, name):
        return object()


class ConditionalWriteError(RuntimeError):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class MemoryTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None, **kwargs):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression == (
            "attribute_not_exists(PK) OR lease_expires_at_epoch <= :now"
        ):
            existing = self.items.get(key)
            now = kwargs["ExpressionAttributeValues"][":now"]
            if existing and existing.get("lease_expires_at_epoch", now + 1) > now:
                raise ConditionalWriteError("active execution lease")
        elif ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if key in self.items:
                raise ConditionalWriteError("conditional write failed")
        self.items[key] = copy.deepcopy(Item)
        return {}

    def delete_item(
        self, *, Key, ConditionExpression=None, ExpressionAttributeValues=None
    ):
        key = (Key["PK"], Key["SK"])
        existing = self.items.get(key)
        if ConditionExpression == "lease_owner = :owner" and (
            not existing
            or existing.get("lease_owner")
            != (ExpressionAttributeValues or {}).get(":owner")
        ):
            raise ConditionalWriteError("stale execution lease owner")
        self.items.pop(key, None)
        return {}

    def get_item(self, *, Key, ConsistentRead):
        assert ConsistentRead is True
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}

    def query(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        return {
            "Items": [
                copy.deepcopy(value)
                for (_pk, sk), value in self.items.items()
                if sk.startswith(aws_training.SELECTION_SK_PREFIX)
            ]
        }


class MemoryDdbResource:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        assert name == "table"
        return self.table


def execution_lease_store():
    table = MemoryTable()
    store = aws_training.AwsTrainingStore(
        table_name="table",
        artifacts_bucket="bucket",
        dynamodb_resource=MemoryDdbResource(table),
        s3_client=FakeS3(),
    )
    return store, table


def real_resource_transaction_store():
    import boto3
    from botocore.config import Config

    resource = boto3.resource(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:9",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(
            connect_timeout=1,
            read_timeout=1,
            retries={"total_max_attempts": 1, "mode": "standard"},
        ),
    )
    store = aws_training.AwsTrainingStore(
        table_name="table",
        artifacts_bucket="bucket",
        dynamodb_resource=resource,
        s3_client=FakeS3(),
    )
    captured = {}

    def capture_request(*, params, **_kwargs):
        captured["body"] = json.loads(params["body"].decode("utf-8"))
        raise CapturedDynamoRequest("request captured before network I/O")

    store.table.meta.client.meta.events.register(
        "before-call.dynamodb.TransactWriteItems",
        capture_request,
    )
    return store, captured


def test_real_boto_resource_serializes_training_status_transaction_once():
    store, captured = real_resource_transaction_store()
    store._get_data = lambda _key: None
    status = {
        "ok": True,
        "status": "WAITING_FOR_EXPERIMENT_MANIFEST",
        "executionMode": "selection_capture",
        "runId": "status-request-shape",
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "createdAtUtc": NOW.isoformat(),
        "statusFingerprintVersion": aws_training.STATUS_FINGERPRINT_VERSION,
    }
    status["statusFingerprint"] = aws_training._status_fingerprint(status)

    with pytest.raises(CapturedDynamoRequest, match="before network I/O"):
        store.save_status(experiment.PRODUCTION_EXPERIMENT_ID, status)

    puts = [entry["Put"] for entry in captured["body"]["TransactItems"]]
    assert len(puts) == 3
    expected_pk = aws_training._experiment_pk(experiment.PRODUCTION_EXPERIMENT_ID)
    assert all(put["Item"]["PK"] == {"S": expected_pk} for put in puts)
    assert all(put["Item"]["SK"].keys() == {"S"} for put in puts)
    assert puts[0]["Item"]["data"]["M"]["ok"] == {"BOOL": True}
    assert puts[1]["ExpressionAttributeValues"][":created"] == {
        "S": NOW.isoformat()
    }


def test_real_boto_resource_serializes_candidate_transaction_once():
    store, captured = real_resource_transaction_store()
    manifest = {
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "revision": 1,
        "manifestDigest": "new-manifest-digest",
        "updatedAtUtc": NOW.isoformat(),
    }
    candidate = {
        "artifactDigest": "candidate-digest",
        "createdAtUtc": NOW.isoformat(),
    }

    with pytest.raises(CapturedDynamoRequest, match="before network I/O"):
        store.commit_candidate(
            manifest,
            candidate,
            expected_revision=0,
            expected_digest="old-manifest-digest",
        )

    puts = [entry["Put"] for entry in captured["body"]["TransactItems"]]
    assert len(puts) == 3
    assert puts[0]["Item"]["revision"] == {"N": "1"}
    assert puts[0]["Item"]["PK"] == {
        "S": aws_training._experiment_pk(experiment.PRODUCTION_EXPERIMENT_ID)
    }
    assert puts[0]["ExpressionAttributeValues"] == {
        ":revision": {"N": "0"},
        ":digest": {"S": "old-manifest-digest"},
    }
    assert puts[1]["Item"]["artifactDigest"] == {"S": "candidate-digest"}


def test_global_lease_reuses_live_r2_key_and_serializes_new_r3_runtime():
    store, table = execution_lease_store()

    acquired = store.acquire_execution_lease(
        aws_training.EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID,
        owner_token="live-r2-training-owner",
        execution_mode="training",
        acquired_at=NOW,
        lease_seconds=aws_training.EXECUTION_LEASE_SECONDS,
    )

    assert aws_training.EXECUTION_LEASE_PK == aws_training._experiment_pk(
        "mlb-v2-2026-07-21-future-prospective-r2"
    )
    assert acquired["PK"] == aws_training.EXECUTION_LEASE_PK
    assert acquired["SK"] == aws_training.EXECUTION_LEASE_SK
    assert acquired["experiment_id"] == (
        aws_training.EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
    )
    assert experiment.PRODUCTION_EXPERIMENT_ID != (
        aws_training.EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID
    )
    assert acquired["execution_mode"] == "training"
    assert acquired["lease_expires_at_epoch"] == int(
        (NOW + aws_training.timedelta(seconds=aws_training.EXECUTION_LEASE_SECONDS)).timestamp()
    )
    with pytest.raises(
        aws_training.ExecutionLeaseUnavailable, match="holds the execution lease"
    ):
        store.acquire_execution_lease(
            experiment.PRODUCTION_EXPERIMENT_ID,
            owner_token="new-r3-capture-owner",
            execution_mode="selection_capture",
            acquired_at=NOW + aws_training.timedelta(minutes=1),
            lease_seconds=aws_training.EXECUTION_LEASE_SECONDS,
        )
    assert len(table.items) == 1
    assert next(iter(table.items.values()))["lease_owner"] == (
        "live-r2-training-owner"
    )


def test_real_store_r3_lease_attests_against_global_migration_key():
    store, _table = execution_lease_store()
    owner = "new-r3-training-owner"
    lease = store.acquire_execution_lease(
        experiment.PRODUCTION_EXPERIMENT_ID,
        owner_token=owner,
        execution_mode="training",
        acquired_at=NOW,
        lease_seconds=aws_training.EXECUTION_LEASE_SECONDS,
    )
    training = aws_training.TrainingService(
        store,
        config(),
        row_loader=lambda _config: [],
        now=lambda: NOW,
    )

    training.attest_execution_lease_acquired(
        lease,
        owner_token=owner,
        execution_mode="training",
    )

    assert training._require_execution_lease("training") == {
        "PK": aws_training.EXECUTION_LEASE_PK,
        "SK": aws_training.EXECUTION_LEASE_SK,
        "experimentId": experiment.PRODUCTION_EXPERIMENT_ID,
        "leaseOwner": owner,
        "executionMode": "training",
        "leaseExpiresAtEpoch": int(
            (
                NOW
                + aws_training.timedelta(
                    seconds=aws_training.EXECUTION_LEASE_SECONDS
                )
            ).timestamp()
        ),
    }


def test_expired_execution_lease_is_reclaimed_and_stale_owner_cannot_release_it():
    store, table = execution_lease_store()
    store.acquire_execution_lease(
        aws_training.EXECUTION_LEASE_MIGRATION_ANCHOR_EXPERIMENT_ID,
        owner_token="expired-owner",
        execution_mode="training",
        acquired_at=NOW,
        lease_seconds=aws_training.EXECUTION_LEASE_SECONDS,
    )
    reclaimed_at = NOW + aws_training.timedelta(
        seconds=aws_training.EXECUTION_LEASE_SECONDS
    )

    replacement = store.acquire_execution_lease(
        experiment.PRODUCTION_EXPERIMENT_ID,
        owner_token="replacement-owner",
        execution_mode="selection_capture",
        acquired_at=reclaimed_at,
        lease_seconds=aws_training.EXECUTION_LEASE_SECONDS,
    )

    assert replacement["lease_owner"] == "replacement-owner"
    with pytest.raises(
        aws_training.ConditionalStateConflict,
        match="ownership changed before release",
    ):
        store.release_execution_lease(
            experiment.PRODUCTION_EXPERIMENT_ID,
            owner_token="expired-owner",
        )
    assert next(iter(table.items.values()))["lease_owner"] == "replacement-owner"
    store.release_execution_lease(
        experiment.PRODUCTION_EXPERIMENT_ID,
        owner_token="replacement-owner",
    )
    assert table.items == {}


def test_execution_lease_duration_must_match_timeout_safe_retry_contract():
    store, _table = execution_lease_store()

    with pytest.raises(
        aws_training.TrainingContractError,
        match="duration does not match the production contract",
    ):
        store.acquire_execution_lease(
            experiment.PRODUCTION_EXPERIMENT_ID,
            owner_token="owner",
            execution_mode="training",
            acquired_at=NOW,
            lease_seconds=900,
        )


def selection_store_and_entry(*, probability=0.72, captured_at=NOW):
    manifest = new_manifest(sealed=True)
    table = MemoryTable()
    table.items[(aws_training._experiment_pk(experiment.PRODUCTION_EXPERIMENT_ID), aws_training.MANIFEST_SK)] = {
        "PK": aws_training._experiment_pk(experiment.PRODUCTION_EXPERIMENT_ID),
        "SK": aws_training.MANIFEST_SK,
        "data": copy.deepcopy(manifest),
    }
    store = aws_training.AwsTrainingStore(
        table_name="table",
        artifacts_bucket="bucket",
        dynamodb_resource=MemoryDdbResource(table),
        s3_client=FakeS3(),
    )
    row = {
        "gameId": "mlb_statsapi:selection-store-1",
        "officialGamePk": "selection-store-1",
        "slateDateEt": "2026-09-01",
        "commenceTime": "2026-09-01T14:00:00+00:00",
        "featureSnapshot": {"fingerprint": "a" * 64},
    }
    entry = experiment.selection_ledger_entry(
        manifest,
        row,
        reliability_probability=probability,
        deployment_identity={"gitSha": "a" * 40, "templateSha256": "b" * 64},
        captured_at_utc=captured_at.isoformat(),
    )
    return store, table, manifest, row, entry


def test_selection_store_retry_preserves_first_capture_and_rejects_changed_decision():
    store, _table, manifest, row, first_entry = selection_store_and_entry()

    first = store.record_selection(first_entry)
    retry_entry = experiment.selection_ledger_entry(
        manifest,
        row,
        reliability_probability=0.72,
        deployment_identity={"gitSha": "a" * 40, "templateSha256": "b" * 64},
        captured_at_utc=(NOW + aws_training.timedelta(minutes=5)).isoformat(),
    )
    retry = store.record_selection(retry_entry)

    assert first["created"] is True
    assert retry["created"] is False
    assert retry["capturedAtUtc"] == first_entry["capturedAtUtc"]
    assert retry["recordFingerprint"] == first_entry["recordFingerprint"]
    assert retry_entry["recordFingerprint"] != first_entry["recordFingerprint"]

    changed = experiment.selection_ledger_entry(
        manifest,
        row,
        reliability_probability=0.71,
        deployment_identity={"gitSha": "a" * 40, "templateSha256": "b" * 64},
        captured_at_utc=(NOW + aws_training.timedelta(minutes=5)).isoformat(),
    )
    with pytest.raises(
        aws_training.ConditionalStateConflict,
        match="immutable prospective selection changed",
    ):
        store.record_selection(changed)


def test_selection_store_validates_semantics_and_complete_readback_envelope():
    store, table, _manifest, _row, entry = selection_store_and_entry()
    invalid = copy.deepcopy(entry)
    invalid["outcomeKnownAtCapture"] = True
    invalid["decisionFingerprint"] = experiment.selection_decision_fingerprint(invalid)
    invalid["recordFingerprint"] = experiment.selection_record_fingerprint(invalid)

    with pytest.raises(
        aws_training.TrainingContractError,
        match="prospective selection contract is invalid",
    ):
        store.record_selection(invalid)

    store.record_selection(entry)
    selection_key = next(
        key for key in table.items if key[1].startswith(aws_training.SELECTION_SK_PREFIX)
    )
    table.items[selection_key]["recordFingerprint"] = "0" * 64
    with pytest.raises(
        aws_training.TrainingContractError,
        match="selection ledger readback is invalid",
    ):
        store.list_selections(experiment.PRODUCTION_EXPERIMENT_ID)


def test_artifact_store_requires_bucket_versioning():
    store = aws_training.AwsTrainingStore(
        table_name="table",
        artifacts_bucket="bucket",
        dynamodb_resource=FakeDdbResource(),
        s3_client=FakeS3(versioning="Suspended"),
    )
    with pytest.raises(
        aws_training.TrainingContractError, match="versioning must be Enabled"
    ):
        store.put_versioned_json("key.json", {"ok": True})
