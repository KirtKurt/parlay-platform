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
import mlb_ml_dual_model_v2 as dual_model
import mlb_ml_experiment_v2 as experiment
import mlb_r7_source_honest_training_repair as repair


SOURCE_AT = "2026-08-24T12:00:00+00:00"
PERSISTED_AT = "2026-08-24T12:05:00+00:00"
LOCK_AT = "2026-08-24T12:15:00+00:00"


def incomplete_snapshot():
    row = {
        "gameId": "r7-source-honest-game",
        "officialGamePk": "999001",
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


def exact_locked_row():
    snapshot = incomplete_snapshot()
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
        "fingerprint": "r7-source-honest-vector",
        "sourcePullAtUtc": SOURCE_AT,
        "lockAtUtc": LOCK_AT,
        "predictionPersistedAtUtc": PERSISTED_AT,
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
    return {
        "gameId": "r7-source-honest-game",
        "officialGamePk": "999001",
        "slateDateEt": "2026-08-24",
        "commenceTime": "2026-08-24T23:00:00+00:00",
        "predictionPersistedAtUtc": PERSISTED_AT,
        "lockedPrediction": True,
        "immutablePerGameStage": True,
        "exactVectorVerified": True,
        "trainingEligible": True,
        "frozenFeatureVector": vector,
        "featureSnapshot": copy.deepcopy(vector),
        "fundamentalsSnapshotV2": snapshot,
        "fundamentalsSnapshotRefV2": {
            "version": snapshot["version"],
            "fingerprint": snapshot["fingerprint"],
        },
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

    assert ok is True, reasons
    assert reasons == []
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


def test_label_join_is_repaired_in_memory_only_for_exact_safe_missingness():
    class FakeLabels:
        @staticmethod
        def _training_verdict(row):
            return False, list(
                row["fundamentalsSnapshotV2"]["trainingExclusionReasons"]
            )

        @staticmethod
        def _joined_training_row(
            slate_date, label, locked, *, slate_finalized
        ):
            return {
                "gameId": locked["gameId"],
                "slateDateEt": slate_date,
                "slateFinalized": slate_finalized,
                "labelStatus": "FINAL",
                "labelFingerprint": label["settlement_fingerprint"],
                "labelRecordFingerprint": label["record_fingerprint"],
                "trainingEligible": False,
                "trainingExclusionReasons": sorted(
                    {
                        *label.get("training_exclusion_reasons", []),
                        *locked["fundamentalsSnapshotV2"][
                            "trainingExclusionReasons"
                        ],
                    }
                ),
            }

    locked = exact_locked_row()
    label = {
        "settlement_fingerprint": "settlement-1",
        "record_fingerprint": "label-record-1",
        "training_eligible": False,
        "training_exclusion_reasons": list(
            locked["fundamentalsSnapshotV2"]["trainingExclusionReasons"]
        ),
    }
    locked_before = copy.deepcopy(locked)
    label_before = copy.deepcopy(label)

    repair._install_label_patch(FakeLabels)
    assert FakeLabels._training_verdict(locked) == (True, [])
    joined = FakeLabels._joined_training_row(
        "2026-08-24",
        label,
        locked,
        slate_finalized=True,
    )

    assert joined["trainingEligible"] is True
    assert joined["trainingExclusionReasons"] == []
    assert joined["r7SourceHonestTrainingAdmission"] is True
    assert joined["productionPickEligibilityChanged"] is False
    assert locked == locked_before
    assert label == label_before

    unsafe = copy.deepcopy(locked)
    unsafe["exactVectorVerified"] = False
    eligible, reasons = FakeLabels._training_verdict(unsafe)
    assert eligible is False
    assert reasons


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
