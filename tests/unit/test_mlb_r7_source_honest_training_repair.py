from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_snapshot_v2 as fundamentals
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v2 as dual_model
import mlb_ml_experiment_v2 as experiment
import mlb_prospective_trainer_read_repair as prospective_read_repair
import mlb_r7_source_honest_training_repair as repair


SOURCE_AT = "2026-08-24T12:00:00+00:00"
PERSISTED_AT = "2026-08-24T12:05:00+00:00"
LOCK_AT = "2026-08-24T12:15:00+00:00"
OFFICIAL_GAME_PK = "999001"
LOCK_PK = "GAME_WINNERS#mlb#2026-08-24"
LOCK_SK = "LOCKED#GAME#2026-08-24T23:00:00+00:00#r7-source-honest-game"
STAGE_FINGERPRINT = "stage-r7-source-honest"


def incomplete_snapshot():
    row = {
        "gameId": "r7-source-honest-game",
        "officialGamePk": OFFICIAL_GAME_PK,
        "slateDateEt": "2026-08-24",
        "predictionSourcePullAt": SOURCE_AT,
        "advanced_context": {"fixture": True},
    }
    for _output_name, context_name, _fields in fundamentals.GROUP_SPECS:
        row["advanced_context"][context_name] = {
            "source_status": "NOT_CONNECTED_SOURCE_REQUIRED",
            "reason": "fixture source unavailable before lock",
        }
    return fundamentals.build(row, captured_at_utc=PERSISTED_AT)


def canonical_authority():
    return {
        "verified": True,
        "consistentRead": True,
        "immutableLocked": True,
        "stageAuthorityVerified": True,
        "persistedStageAuthorityValidated": True,
        "officialAuditEligible": True,
        "exactLockVectorValidated": True,
        "selectionLockVectorStatusValidated": True,
        "sourcePk": LOCK_PK,
        "sourceSk": LOCK_SK,
        "stageFingerprint": STAGE_FINGERPRINT,
        "officialGamePk": OFFICIAL_GAME_PK,
    }


def exact_locked_row():
    snapshot = incomplete_snapshot()
    snapshot_ref = {
        "version": snapshot["version"],
        "schemaCohort": snapshot["schemaCohort"],
        "gameId": snapshot["game"]["gameId"],
        "sourcePullId": snapshot["sourcePullId"],
        "evidenceCutoffUtc": snapshot["evidenceCutoffUtc"],
        "fingerprintVersion": snapshot["fingerprintVersion"],
        "fingerprint": snapshot["fingerprint"],
    }
    features = {
        name: float(index + 1) / 100.0
        for index, name in enumerate(
            sorted(
                {
                    *dual_model.OUTCOME_FEATURES,
                    *dual_model.RELIABILITY_FEATURES,
                }
                - {
                    "homeMarketDeVigProbability",
                    "selectedMarketDeVigProbability",
                    "starterCompositeGapHome",
                    "bullpenCompositeGapHome",
                    "lineupWrcPlusGapHome",
                    "fundamentalPitchingMissing",
                    "fundamentalOffenseLineupMissing",
                }
            )
        )
    }
    vector = {
        "version": "MLB-VECTOR-v2",
        "fingerprintVersion": cohort.FINGERPRINT_VERSION,
        "gameId": "r7-source-honest-game",
        "officialGamePk": OFFICIAL_GAME_PK,
        "slateDateEt": "2026-08-24",
        "sourcePullAtUtc": SOURCE_AT,
        "lockAtUtc": LOCK_AT,
        "predictionPersistedAtUtc": PERSISTED_AT,
        "labels": {"homeWon": None, "pickCorrect": None},
        "fundamentalsSnapshotV2Version": snapshot["version"],
        "fundamentalsSnapshotV2SchemaCohort": snapshot["schemaCohort"],
        "fundamentalsSnapshotV2FingerprintVersion": snapshot[
            "fingerprintVersion"
        ],
        "fundamentalsSnapshotV2Fingerprint": snapshot["fingerprint"],
        "fundamentalsSnapshotV2EvidenceCutoffUtc": snapshot[
            "evidenceCutoffUtc"
        ],
        "fundamentalsSnapshotV2AtOrBeforeLock": True,
        "fundamentalsSnapshotV2Ref": copy.deepcopy(snapshot_ref),
        "features": features,
        "pullHistoryIntegrity": {
            "version": "INQSI-PULL-HISTORY-INTEGRITY-v1-canonical-quarter-hour",
            "canonicalizationVersion": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
            "rawPullCount": 1,
            "uniqueSlotCount": 1,
            "duplicatePullCount": 0,
            "invalidPullCount": 0,
            "contaminatedSlotCount": 0,
            "duplicateContaminated": False,
            "canonicalSlotFingerprint": "r7-source-honest-slots",
            "slotStartsUtc": [SOURCE_AT],
        },
        "predictionSourceCanonicalSlot": {
            "version": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
            "slotStartUtc": SOURCE_AT,
            "canonical": True,
            "canonicalPullFingerprint": "r7-source-honest-pull",
            "duplicatePullCount": 0,
            "invalidPullCount": 0,
            "contaminated": False,
        },
    }
    vector["fingerprint"] = cohort.fingerprint_for_vector(vector)
    return {
        "gameId": "r7-source-honest-game",
        "officialGamePk": OFFICIAL_GAME_PK,
        "slateDateEt": "2026-08-24",
        "commenceTime": "2026-08-24T23:00:00+00:00",
        "homeMarketDeVigProbability": 0.6125,
        "awayMarketDeVigProbability": 0.3875,
        "marketProbability": 0.6125,
        "marketProbabilitySourceAtUtc": SOURCE_AT,
        "marketProbabilityVersion": (
            "MLB-MARKET-DEVIG-BASELINE-v1-canonical-pull-slot"
        ),
        "marketProbabilityFingerprint": "immutable-market-fingerprint",
        "predictionPersistedAtUtc": PERSISTED_AT,
        "lockedPrediction": True,
        "immutablePerGameStage": True,
        "immutableLockedStorage": True,
        "exactVectorVerified": True,
        "trainingEligible": True,
        "frozenFeatureVector": vector,
        "featureSnapshot": copy.deepcopy(vector),
        "fundamentalsSnapshotV2": snapshot,
        "fundamentalsSnapshotRefV2": snapshot_ref,
        "canonicalLockAuthority": canonical_authority(),
    }


def exact_label(locked):
    snapshot = locked["fundamentalsSnapshotV2"]
    vector = locked["frozenFeatureVector"]
    return {
        "official_game_pk": OFFICIAL_GAME_PK,
        "completed": True,
        "write_once": True,
        "settlement_fingerprint": "settlement-1",
        "record_fingerprint": "label-record-1",
        "canonical_lock_pk": LOCK_PK,
        "canonical_lock_sk": LOCK_SK,
        "canonical_stage_fingerprint": STAGE_FINGERPRINT,
        "canonical_lock_payload_fingerprint": "locked-payload-1",
        "frozen_feature_vector_fingerprint": vector["fingerprint"],
        "fundamentals_snapshot_v2_version": snapshot["version"],
        "fundamentals_snapshot_v2_fingerprint": snapshot["fingerprint"],
        "training_eligible": False,
        "training_exclusion_reasons": list(
            snapshot["trainingExclusionReasons"]
        ),
    }


def production_manifest():
    return experiment.new_manifest(
        experiment_id=experiment.PRODUCTION_EXPERIMENT_ID,
        release_contract_id=experiment.PRODUCTION_RELEASE_CONTRACT_ID,
        release_cutoff_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
        feature_vector_version="MLB-VECTOR-v2",
        model_feature_schemas={
            "outcome": list(dual_model.OUTCOME_FEATURES),
            "reliability": list(dual_model.RELIABILITY_FEATURES),
        },
        created_at_utc=experiment.PRODUCTION_RELEASE_CUTOFF_UTC,
    )


def test_source_honest_incomplete_snapshot_is_lock_safe_but_not_a_full_data_pick():
    snapshot = incomplete_snapshot()
    ok, reasons = repair.validate_snapshot_for_r7_training(
        snapshot,
        PERSISTED_AT,
        LOCK_AT,
    )
    full_data_ok, full_data_reasons = fundamentals.validate_snapshot(
        snapshot,
        prediction_time_utc=PERSISTED_AT,
        lock_time_utc=LOCK_AT,
    )

    assert ok is True, reasons
    assert reasons == []
    assert full_data_ok is False
    assert full_data_reasons == snapshot["trainingExclusionReasons"]
    assert snapshot["pregameComplete"] is False
    assert snapshot["trainingEligibleAtCapture"] is False
    assert snapshot["trainingExclusionReasons"]
    assert all(
        reason.startswith(repair.INCOMPLETE_PREFIX)
        for reason in snapshot["trainingExclusionReasons"]
    )


def test_source_honest_policy_rejects_tamper_and_post_lock_evidence():
    tampered = incomplete_snapshot()
    tampered["missingGroups"] = []
    assert repair.validate_snapshot_for_r7_training(
        tampered,
        PERSISTED_AT,
        LOCK_AT,
    )[0] is False

    assert repair.validate_snapshot_for_r7_training(
        incomplete_snapshot(),
        "2026-08-24T12:20:00+00:00",
        LOCK_AT,
    ) == (
        False,
        ["r7_fundamentals_v2_evidence_not_lock_safe"],
    )


def fake_labels_module():
    label_record_validation_calls = []

    def training_verdict(row):
        return False, list(
            row["fundamentalsSnapshotV2"]["trainingExclusionReasons"]
        )

    def trusted_label_record_errors(item, slate_date, official_game_pk):
        """Explicit trusted test stub for the production validation hook."""

        label_record_validation_calls.append(
            (copy.deepcopy(item), slate_date, official_game_pk)
        )
        errors = []
        if not isinstance(item, dict):
            return ["canonical_label_record_missing"]
        if slate_date != "2026-08-24":
            errors.append("canonical_label_key_mismatch")
        if str(item.get("official_game_pk") or "") != official_game_pk:
            errors.append("canonical_label_official_game_pk_mismatch")
        if item.get("write_once") is not True or item.get("completed") is not True:
            errors.append("canonical_label_write_once_or_final_flag_missing")
        if not str(item.get("settlement_fingerprint") or ""):
            errors.append("canonical_label_settlement_fingerprint_mismatch")
        if not str(item.get("record_fingerprint") or ""):
            errors.append("canonical_label_record_fingerprint_mismatch")
        return sorted(set(errors))

    def joined_training_row(slate_date, label, locked, *, slate_finalized):
        vector = copy.deepcopy(locked["frozenFeatureVector"])
        snapshot = copy.deepcopy(locked["fundamentalsSnapshotV2"])
        return {
            "gameId": locked["gameId"],
            "officialGamePk": label["official_game_pk"],
            "providerEventId": label.get("provider_event_id"),
            "slateDateEt": slate_date,
            "slateFinalized": slate_finalized,
            "commenceTime": locked["commenceTime"],
            "homeTeam": locked.get("homeTeam"),
            "awayTeam": locked.get("awayTeam"),
            "predictedWinner": locked.get("predictedWinner"),
            "predictedSide": locked.get("predictedSide"),
            "lockedAmericanOdds": locked.get(
                "lockedAmericanOdds",
                locked.get("americanOdds"),
            ),
            **{
                field: copy.deepcopy(locked[field])
                for field in repair.MARKET_PROBABILITY_PROJECTION_FIELDS
                if field in locked and locked[field] not in (None, "")
            },
            "predictionPersistedAtUtc": locked["predictionPersistedAtUtc"],
            "featureSnapshot": vector,
            "frozenFeatureVector": copy.deepcopy(vector),
            "fundamentalsSnapshotV2": snapshot,
            "fundamentalsSnapshotV2Ref": copy.deepcopy(
                locked["fundamentalsSnapshotRefV2"]
            ),
            "winner": label.get("winner"),
            "homeWon": label.get("home_won"),
            "correct": label.get("correct"),
            "pickCorrect": label.get("correct"),
            "labelStatus": "FINAL",
            "labelFingerprint": label["settlement_fingerprint"],
            "labelRecordFingerprint": label["record_fingerprint"],
            "labelSource": label.get("source"),
            "labelSourcePayloadFingerprint": label.get(
                "source_payload_fingerprint"
            ),
            "labelRetrievedAtUtc": label.get("observed_at_utc"),
            "canonicalLockPk": label["canonical_lock_pk"],
            "canonicalLockSk": label["canonical_lock_sk"],
            "canonicalStageFingerprint": label[
                "canonical_stage_fingerprint"
            ],
            "trainingEligible": False,
            "trainingExclusionReasons": sorted(
                {
                    *label.get("training_exclusion_reasons", []),
                    *locked["fundamentalsSnapshotV2"][
                        "trainingExclusionReasons"
                    ],
                }
            ),
            "immutablePregameVectorMutated": False,
        }

    return SimpleNamespace(
        _training_verdict=training_verdict,
        _joined_training_row=joined_training_row,
        _label_record_errors=trusted_label_record_errors,
        _label_record_validation_calls=label_record_validation_calls,
        _canonical_lock_payload_fingerprint=(
            lambda locked: "locked-payload-1"
        ),
    )


def test_label_join_is_repaired_in_memory_only_for_exact_bound_missingness():
    labels = fake_labels_module()
    locked = exact_locked_row()
    label = exact_label(locked)
    locked_before = copy.deepcopy(locked)
    label_before = copy.deepcopy(label)

    repair._install_label_patch(labels)
    assert labels._training_verdict(locked) == (True, [])
    joined = labels._joined_training_row(
        "2026-08-24",
        label,
        locked,
        slate_finalized=True,
    )

    assert joined["trainingEligible"] is True
    assert joined["trainingExclusionReasons"] == []
    assert joined["r7SourceHonestTrainingAdmission"] is True
    assert joined["r7SourceHonestLabelLockBindingVersion"] == (
        repair.LABEL_LOCK_BINDING_VERSION
    )
    assert joined["r7SourceHonestJoinedBinding"]["version"] == (
        repair.JOINED_SNAPSHOT_BINDING_VERSION
    )
    assert joined["r7SourceHonestTrustedReceiptId"]
    assert joined["productionPickEligibilityChanged"] is False
    assert repair.row_is_source_honest_training_safe(joined) == (
        True,
        [],
        joined["r7SourceHonestMissingnessMasks"],
    )
    assert labels._label_record_validation_calls == [
        (label_before, "2026-08-24", OFFICIAL_GAME_PK)
    ]
    assert locked == locked_before
    assert label == label_before

    unsafe = copy.deepcopy(locked)
    unsafe["exactVectorVerified"] = False
    unsafe["canonicalLockAuthority"]["exactLockVectorValidated"] = False
    eligible, reasons = labels._training_verdict(unsafe)
    assert eligible is False
    assert reasons

    mismatched = copy.deepcopy(label)
    mismatched["canonical_stage_fingerprint"] = "wrong-stage"
    rejected = labels._joined_training_row(
        "2026-08-24",
        mismatched,
        locked,
        slate_finalized=True,
    )
    assert rejected["trainingEligible"] is False
    assert rejected["r7SourceHonestTrainingAdmission"] is False
    assert "r7_label_canonical_stage_fingerprint_not_bound_to_lock" in (
        rejected["r7SourceHonestSafetyReasons"]
    )


def test_production_compat_install_order_binds_read_repair_and_advances_r7():
    labels = fake_labels_module()
    locked = exact_locked_row()
    label = exact_label(locked)
    locked_before = copy.deepcopy(locked)
    label_before = copy.deepcopy(label)

    # This is the exact trainer compatibility order: the read-only stale-state
    # repair wraps the canonical join before R7 installs its trusted receipt.
    prospective_read_repair.install(labels)
    repair._install_label_patch(labels)
    repair._install_experiment_patch(experiment)

    joined = labels._joined_training_row(
        "2026-08-24",
        label,
        locked,
        slate_finalized=True,
    )

    assert joined["prospectiveTrainerReadRepairVersion"] == (
        prospective_read_repair.VERSION
    )
    assert joined["r7SourceHonestJoinedBinding"][
        "prospectiveTrainerReadRepairVersion"
    ] == prospective_read_repair.VERSION
    assert joined["trainingEligible"] is True
    assert joined["r7SourceHonestTrainingAdmission"] is True
    safe, reasons, masks = repair.row_is_source_honest_training_safe(joined)
    assert safe is True, reasons
    assert reasons == []
    assert masks == joined["r7SourceHonestMissingnessMasks"]
    assert {
        field: joined[field]
        for field in repair.MARKET_PROBABILITY_PROJECTION_FIELDS
    } == {
        field: locked[field]
        for field in repair.MARKET_PROBABILITY_PROJECTION_FIELDS
    }

    manifest = production_manifest()
    valid, validation_reasons = experiment.validate_record(joined, manifest)
    assert valid is True, validation_reasons
    advanced = experiment.advance_manifest(
        manifest,
        [joined],
        finalized_slate_dates=["2026-08-24"],
        updated_at_utc="2026-08-25T12:00:00+00:00",
    )
    assert advanced["partitions"]["train"]["rowCount"] == 1
    assert advanced["assignedSlateDates"]["2026-08-24"]["rowCount"] == 1
    assert locked == locked_before
    assert label == label_before

    tampered = copy.deepcopy(joined)
    tampered["prospectiveTrainerReadRepairVersion"] = "tampered-version"
    tampered["r7SourceHonestJoinedBinding"][
        "prospectiveTrainerReadRepairVersion"
    ] = "tampered-version"
    tamper_safe, tamper_reasons, tamper_masks = (
        repair.row_is_source_honest_training_safe(tampered)
    )
    assert tamper_safe is False
    assert "r7_joined_trusted_receipt_material_mismatch" in tamper_reasons
    assert tamper_masks == {}


def test_joined_receipt_binds_exact_market_projection_without_reconstruction():
    labels = fake_labels_module()
    locked = exact_locked_row()
    locked.pop("awayMarketDeVigProbability")
    repair._install_label_patch(labels)

    joined = labels._joined_training_row(
        "2026-08-24",
        exact_label(locked),
        locked,
        slate_finalized=True,
    )

    assert "awayMarketDeVigProbability" not in joined
    assert repair.row_is_source_honest_training_safe(joined)[0] is True

    tampered = copy.deepcopy(joined)
    tampered["marketProbabilityFingerprint"] = "changed-market-fingerprint"
    safe, reasons, masks = repair.row_is_source_honest_training_safe(tampered)
    assert safe is False
    assert reasons == ["r7_joined_trusted_receipt_material_mismatch"]
    assert masks == {}


def test_exact_read_repair_annotation_without_installed_wrapper_fails_closed():
    labels = fake_labels_module()
    underlying_join = labels._joined_training_row

    def self_annotated_join(*args, **kwargs):
        row = underlying_join(*args, **kwargs)
        row["prospectiveTrainerReadRepairVersion"] = (
            prospective_read_repair.VERSION
        )
        return row

    labels._joined_training_row = self_annotated_join
    repair._install_label_patch(labels)
    locked = exact_locked_row()
    joined = labels._joined_training_row(
        "2026-08-24",
        exact_label(locked),
        locked,
        slate_finalized=True,
    )

    assert joined["trainingEligible"] is False
    assert joined["r7SourceHonestTrainingAdmission"] is False
    assert "r7_untrusted_prospective_read_repair_annotation" in joined[
        "r7SourceHonestSafetyReasons"
    ]


def test_label_binding_failure_forces_underlying_eligible_join_closed():
    labels = fake_labels_module()
    underlying_join = labels._joined_training_row

    def incorrectly_eligible_join(*args, **kwargs):
        row = underlying_join(*args, **kwargs)
        row["trainingEligible"] = True
        row["trainingExclusionReasons"] = []
        return row

    labels._joined_training_row = incorrectly_eligible_join
    locked = exact_locked_row()
    label = exact_label(locked)
    label["canonical_lock_payload_fingerprint"] = "wrong-lock-payload"
    repair._install_label_patch(labels)

    rejected = labels._joined_training_row(
        "2026-08-24",
        label,
        locked,
        slate_finalized=True,
    )

    assert rejected["trainingEligible"] is False
    assert rejected["r7SourceHonestTrainingAdmission"] is False
    assert (
        "r7_label_canonical_lock_payload_fingerprint_not_bound_to_lock"
        in rejected["trainingExclusionReasons"]
    )


def test_training_verdict_forces_underlying_eligible_result_closed_when_unsafe():
    labels = fake_labels_module()
    labels._training_verdict = lambda _row: (True, [])
    repair._install_label_patch(labels)
    unsafe = exact_locked_row()
    unsafe["exactVectorVerified"] = False
    unsafe["canonicalLockAuthority"]["exactLockVectorValidated"] = False

    eligible, reasons = labels._training_verdict(unsafe)

    assert eligible is False
    assert "r7_exact_vector_proof_missing" in reasons


def test_joined_row_rejects_full_self_consistent_forgery_without_new_receipt():
    labels = fake_labels_module()
    locked = exact_locked_row()
    repair._install_label_patch(labels)
    joined = labels._joined_training_row(
        "2026-08-24",
        exact_label(locked),
        locked,
        slate_finalized=True,
    )

    forged = copy.deepcopy(joined)
    snapshot = forged["fundamentalsSnapshotV2"]
    snapshot["groups"]["starter_quality"]["missingReason"] = (
        "schema-valid recomputed snapshot"
    )
    snapshot["fingerprint"] = fundamentals.fingerprint_for_snapshot(snapshot)
    reference = forged["fundamentalsSnapshotV2Ref"]
    reference["fingerprint"] = snapshot["fingerprint"]
    vector = forged["frozenFeatureVector"]
    vector["fundamentalsSnapshotV2Fingerprint"] = snapshot["fingerprint"]
    vector["fundamentalsSnapshotV2Ref"] = copy.deepcopy(reference)
    vector["fingerprint"] = cohort.fingerprint_for_vector(vector)
    forged["featureSnapshot"] = copy.deepcopy(vector)

    forged["labelFingerprint"] = "forged-settlement-fingerprint"
    forged["labelRecordFingerprint"] = "forged-label-record-fingerprint"
    forged["canonicalStageFingerprint"] = "forged-stage-fingerprint"
    forged["canonicalLockPayloadFingerprint"] = (
        "forged-lock-payload-fingerprint"
    )
    binding = forged["r7SourceHonestJoinedBinding"]
    binding.update(
        {
            "snapshotFingerprint": snapshot["fingerprint"],
            "referenceFingerprint": reference["fingerprint"],
            "vectorSnapshotFingerprint": snapshot["fingerprint"],
            "vectorFingerprint": vector["fingerprint"],
            "stageFingerprint": forged["canonicalStageFingerprint"],
            "lockPayloadFingerprint": forged[
                "canonicalLockPayloadFingerprint"
            ],
            "settlementFingerprint": forged["labelFingerprint"],
            "recordFingerprint": forged["labelRecordFingerprint"],
            "labelVectorFingerprint": vector["fingerprint"],
            "labelSnapshotFingerprint": snapshot["fingerprint"],
        }
    )
    assert fundamentals.validate(snapshot) == []
    assert vector["fingerprint"] == cohort.fingerprint_for_vector(vector)
    assert forged["r7SourceHonestTrainingAdmission"] is True

    safe, reasons, _masks = repair.row_is_source_honest_training_safe(forged)
    assert safe is False
    assert reasons == ["r7_joined_trusted_receipt_material_mismatch"]


def test_joined_row_without_trusted_receipt_fails_closed():
    labels = fake_labels_module()
    locked = exact_locked_row()
    repair._install_label_patch(labels)
    joined = labels._joined_training_row(
        "2026-08-24",
        exact_label(locked),
        locked,
        slate_finalized=True,
    )
    joined.pop("r7SourceHonestTrustedReceiptId")

    safe, reasons, masks = repair.row_is_source_honest_training_safe(joined)

    assert safe is False
    assert reasons == ["r7_joined_trusted_receipt_missing_or_evicted"]
    assert masks == {}


def test_v3_install_exposes_joined_binding_and_receipt_versions():
    labels = fake_labels_module()

    status = repair.install(
        labels=labels,
        experiment=experiment,
        dual_model=dual_model,
    )

    assert status["version"] == repair.VERSION
    assert status["joinedSnapshotBindingVersion"] == (
        repair.JOINED_SNAPSHOT_BINDING_VERSION
    )
    assert status["joinedTrustedReceiptVersion"] == (
        repair.JOINED_TRUSTED_RECEIPT_VERSION
    )
    for module in (labels, experiment, dual_model):
        assert module.MLB_R7_JOINED_SNAPSHOT_BINDING_VERSION == (
            repair.JOINED_SNAPSHOT_BINDING_VERSION
        )
        assert module.MLB_R7_JOINED_TRUSTED_RECEIPT_VERSION == (
            repair.JOINED_TRUSTED_RECEIPT_VERSION
        )
    assert getattr(labels, repair._LABEL_PATCH_FLAG) is True
    assert getattr(experiment, repair._EXPERIMENT_PATCH_FLAG) is True
    assert getattr(dual_model, repair._DUAL_PATCH_FLAG) is True
    assert labels._joined_training_row._mlb_r7_joined_snapshot_binding_version == (
        repair.JOINED_SNAPSHOT_BINDING_VERSION
    )


def test_production_r7_experiment_accepts_only_prespecified_masked_missingness():
    repair._install_experiment_patch(experiment)
    row = exact_locked_row()
    manifest = production_manifest()

    ok, reasons = experiment.validate_record(row, manifest)
    assert ok is True, reasons
    assert reasons == []

    no_mask = copy.deepcopy(manifest)
    no_mask["featureNames"] = [
        value
        for value in no_mask["featureNames"]
        if value != "fundamentalPitchingMissing"
    ]
    no_mask["modelFeatureSchemas"]["outcome"] = [
        value
        for value in no_mask["modelFeatureSchemas"]["outcome"]
        if value != "fundamentalPitchingMissing"
    ]
    ok, reasons = experiment.validate_record(row, no_mask)
    assert ok is False
    assert any("missingness_mask_absent" in reason for reason in reasons)

    corrupt = copy.deepcopy(row)
    corrupt["fundamentalsSnapshotV2"]["fingerprint"] = "tampered"
    ok, reasons = experiment.validate_record(corrupt, manifest)
    assert ok is False
    assert "fundamentals_v2_fingerprint_mismatch" in reasons


def test_all_missing_optional_fundamentals_are_zero_weight_not_a_training_stop():
    repair._install_dual_model_patch(dual_model)
    features = list(dual_model.OUTCOME_FEATURES)
    records = []
    for index in range(40):
        row = {
            name: (float(index % 7) / 10.0)
            for name in features
            if name not in repair.OPTIONAL_ALL_MISSING_NUMERIC_FEATURES
        }
        for name in repair.OPTIONAL_ALL_MISSING_NUMERIC_FEATURES:
            if name in features:
                row[name] = None
        row["fundamentalPitchingMissing"] = 1.0
        row["fundamentalOffenseLineupMissing"] = 1.0
        row["homeWon"] = index % 2
        records.append(row)

    model = dual_model.fit_logistic(
        records,
        features,
        "homeWon",
        "r7-source-honest-test-model",
        epochs=20,
    )

    assert model["ok"] is True, model
    inactive = set(model["inactiveAllMissingFeatures"])
    assert inactive == (
        set(features) & repair.OPTIONAL_ALL_MISSING_NUMERIC_FEATURES
    )
    assert model["features"] == features
    for name in inactive:
        assert model["weights"][name] == 0.0
        assert model["means"][name] == 0.0
        assert model["scales"][name] == 1.0
