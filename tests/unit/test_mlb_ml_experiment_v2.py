import copy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_snapshot_v2 as fundamentals
import mlb_ml_experiment_v2 as experiment


FEATURE_SCHEMAS = {
    "outcome": [f"outcome_{index}" for index in range(8)],
    "reliability": [f"reliability_{index}" for index in range(10)],
}
DEPLOYMENT_IDENTITY = {"gitSha": "a" * 40, "templateSha256": "b" * 64}


def manifest():
    return experiment.new_manifest(
        experiment_id="mlb-v2-test",
        release_contract_id="release-contract-r1",
        release_cutoff_utc="2026-07-21T00:00:00+00:00",
        feature_vector_version="MLB-VECTOR-v2",
        model_feature_schemas=FEATURE_SCHEMAS,
        created_at_utc="2026-07-21T00:00:00+00:00",
    )


def identity_rows(days=35, games_per_day=15, first=None, label_at=None):
    rows = []
    first = first or date(2026, 7, 21)
    for day_index in range(days):
        slate = (first + timedelta(days=day_index)).isoformat()
        for game_index in range(games_per_day):
            rows.append(
                {
                    "gameId": f"{slate}-{game_index}",
                    "officialGamePk": f"{slate.replace('-', '')}{game_index:02d}",
                    "slateDateEt": slate,
                    "commenceTime": f"{slate}T23:00:00+00:00",
                    "featureSnapshot": {
                        "fingerprint": f"vector-{slate}-{game_index}"
                    },
                    "labelRetrievedAtUtc": label_at or f"{slate}T23:59:00+00:00",
                }
            )
    return rows


def valid_admission_row():
    source_at = "2026-07-21T12:00:00+00:00"
    persisted_at = "2026-07-21T12:05:00+00:00"
    lock_at = "2026-07-21T12:15:00+00:00"
    row = {
        "gameId": "game-1",
        "slateDateEt": "2026-07-21",
        "predictionSourcePullAt": source_at,
        "predictionPersistedAtUtc": persisted_at,
        # Non-empty context keeps this a pure contract test and prevents the
        # builder from invoking any live context source.
        "advanced_context": {"source": "test-fixture"},
        "trainingEligible": True,
    }
    for output_name, context_name, fields in fundamentals.GROUP_SPECS:
        source = {
            "source_status": "CONNECTED",
            "sourceProvenance": {
                "provider": "fixture-provider",
                "endpoint": "https://example.invalid/pregame",
                "dataset": context_name,
                "retrievedAtUtc": source_at,
                "sourceEffectiveAtUtc": source_at,
                "payloadFingerprint": f"fixture-{context_name}",
            },
        }
        required = set(fundamentals.REQUIRED_VALUE_KEYS[output_name])
        for output_key, input_key in fields:
            if output_key in required:
                source[input_key] = 1
        row["advanced_context"][context_name] = source
    snapshot = fundamentals.build(row, captured_at_utc=persisted_at)
    row.update(
        {
            "fundamentalsSnapshotV2": snapshot,
            "fundamentalsSnapshotRefV2": {
                "version": snapshot["version"],
                "fingerprint": snapshot["fingerprint"],
            },
            "featureSnapshot": {
                "version": "MLB-VECTOR-v2",
                "fingerprint": "vector-1",
                "sourcePullAtUtc": source_at,
                "lockAtUtc": lock_at,
                "pullHistoryIntegrity": {
                    "version": "INQSI-PULL-HISTORY-INTEGRITY-v1-canonical-quarter-hour",
                    "canonicalizationVersion": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
                    "rawPullCount": 1,
                    "uniqueSlotCount": 1,
                    "duplicatePullCount": 0,
                    "invalidPullCount": 0,
                    "contaminatedSlotCount": 0,
                    "duplicateContaminated": False,
                    "canonicalSlotFingerprint": "pull-slots-1",
                    "slotStartsUtc": ["2026-07-21T12:00:00+00:00"],
                },
                "predictionSourceCanonicalSlot": {
                    "version": "INQSI-CANONICAL-PULL-SLOT-v1-earliest-integrity-valid",
                    "slotStartUtc": "2026-07-21T12:00:00+00:00",
                    "canonical": True,
                    "canonicalPullFingerprint": "pull-1",
                    "duplicatePullCount": 0,
                    "invalidPullCount": 0,
                    "contaminated": False,
                },
                "features": {
                    name: float(index)
                    for index, name in enumerate(
                        sorted(
                            {
                                value
                                for values in FEATURE_SCHEMAS.values()
                                for value in values
                            }
                        )
                    )
                },
            },
        }
    )
    return row


def full_clean_slate_rows(game_count=3, slate="2026-07-21"):
    rows = []
    for index in range(game_count):
        row = copy.deepcopy(valid_admission_row())
        row.update(
            {
                "gameId": f"{slate}-game-{index}",
                "officialGamePk": f"{slate.replace('-', '')}{index:02d}",
                "slateDateEt": slate,
                "slateFinalized": True,
                "commenceTime": f"{slate}T23:00:00+00:00",
                "labelRetrievedAtUtc": f"{slate}T23:59:00+00:00",
            }
        )
        row["featureSnapshot"]["fingerprint"] = f"vector-{slate}-{index}"
        rows.append(row)
    return rows


def official_slate_authority(rows, *extra_game_pks):
    return experiment.build_official_finalized_slate_authority(
        slate_date_et=rows[0]["slateDateEt"],
        official_game_pks=[
            *(row["officialGamePk"] for row in rows),
            *extra_game_pks,
        ],
        schedule_source="MLB Stats API",
        schedule_source_url="https://statsapi.mlb.com/api/v1/schedule",
    )


def test_clean_filter_fails_closed_on_duplicate_immutable_row():
    row = valid_admission_row()
    with pytest.raises(experiment.ExperimentContractError, match="duplicate immutable vector"):
        experiment.filter_records([row, copy.deepcopy(row)], manifest())


def test_historical_backlog_cannot_fill_future_prospective_partition():
    rows = identity_rows()
    updated = experiment.advance_manifest(
        manifest(),
        list(reversed(rows)),
        finalized_slate_dates={row["slateDateEt"] for row in rows},
        updated_at_utc="2026-09-01T00:00:00+00:00",
    )

    assert updated["prospectiveTestSealed"] is False
    assert {
        name: updated["partitions"][name]["rowCount"]
        for name in experiment.PARTITION_ORDER
    } == {"train": 300, "validation": 105, "prospectiveTest": 0}
    assert updated["phase"] == "AWAITING_PERSISTED_FROZEN_CHALLENGER"
    assert len(updated["historicalDiagnosticSlateDates"]) == 8

    updated = experiment.bind_frozen_challenger(
        updated,
        artifact={
            "bucket": "artifacts",
            "key": "challenger.json",
            "versionId": "v1",
            "sha256": "a" * 64,
        },
        artifact_digest="a" * 64,
        selected_threshold=0.6,
        bound_at_utc="2026-09-01T00:05:00+00:00",
    )
    future = identity_rows(
        days=7,
        first=date(2026, 9, 2),
        label_at="2026-09-10T00:00:00+00:00",
    )
    updated = experiment.advance_manifest(
        updated,
        rows + future,
        finalized_slate_dates={row["slateDateEt"] for row in rows + future},
        updated_at_utc="2026-09-10T01:00:00+00:00",
    )
    assert updated["prospectiveTestSealed"] is True
    assert updated["partitions"]["prospectiveTest"]["rowCount"] == 105
    assigned = updated["assignedSlateDates"]
    for slate_date in sorted(assigned):
        assert assigned[slate_date]["partition"] in experiment.PARTITION_ORDER
    partition_dates = [
        set(updated["partitions"][name]["slateDates"])
        for name in experiment.PARTITION_ORDER
    ]
    assert not (partition_dates[0] & partition_dates[1])
    assert not (partition_dates[0] & partition_dates[2])
    assert not (partition_dates[1] & partition_dates[2])
    assert max(partition_dates[0]) < min(partition_dates[1])
    assert max(partition_dates[1]) < min(partition_dates[2])


def _manifest_with_bound_challenger(*, bound_at_utc):
    historical = identity_rows(days=27)
    frozen = experiment.advance_manifest(
        manifest(),
        historical,
        finalized_slate_dates={row["slateDateEt"] for row in historical},
        updated_at_utc="2026-09-01T00:00:00+00:00",
    )
    assert frozen["partitions"]["train"]["frozen"] is True
    assert frozen["partitions"]["validation"]["frozen"] is True
    bound = experiment.bind_frozen_challenger(
        frozen,
        artifact={
            "bucket": "artifacts",
            "key": "challenger.json",
            "versionId": "v1",
            "sha256": "a" * 64,
        },
        artifact_digest="a" * 64,
        selected_threshold=0.6,
        bound_at_utc=bound_at_utc,
    )
    return historical, bound


def test_late_label_cannot_make_a_pre_cutover_game_prospective():
    historical, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-02T22:00:00+00:00"
    )
    retrospective = identity_rows(
        days=1,
        games_per_day=2,
        first=date(2026, 9, 2),
        label_at="2026-09-03T04:00:00+00:00",
    )
    retrospective[0]["commenceTime"] = "2026-09-02T21:00:00+00:00"
    retrospective[1]["commenceTime"] = "2026-09-02T23:00:00+00:00"

    updated = experiment.advance_manifest(
        bound,
        historical + retrospective,
        finalized_slate_dates={
            row["slateDateEt"] for row in historical + retrospective
        },
        updated_at_utc="2026-09-03T05:00:00+00:00",
    )

    assert updated["partitions"]["prospectiveTest"]["rowCount"] == 0
    assert updated["historicalDiagnosticSlateDates"]["2026-09-02"][
        "prospectiveAuthority"
    ] is False


def test_disappearing_assigned_or_diagnostic_slate_fails_closed():
    rows = identity_rows(days=28)
    frozen = experiment.advance_manifest(
        manifest(),
        rows,
        finalized_slate_dates={row["slateDateEt"] for row in rows},
    )
    assigned_date = sorted(frozen["assignedSlateDates"])[0]
    diagnostic_date = sorted(frozen["historicalDiagnosticSlateDates"])[0]

    without_assigned = [row for row in rows if row["slateDateEt"] != assigned_date]
    with pytest.raises(
        experiment.FrozenPartitionConflict,
        match=f"frozen slate {assigned_date} disappeared",
    ):
        experiment.advance_manifest(
            frozen,
            without_assigned,
            finalized_slate_dates={row["slateDateEt"] for row in without_assigned},
        )

    without_diagnostic = [
        row for row in rows if row["slateDateEt"] != diagnostic_date
    ]
    with pytest.raises(
        experiment.FrozenPartitionConflict,
        match=f"diagnostic historical slate {diagnostic_date} disappeared",
    ):
        experiment.advance_manifest(
            frozen,
            without_diagnostic,
            finalized_slate_dates={row["slateDateEt"] for row in without_diagnostic},
        )


def test_late_older_slate_cannot_cross_into_validation_after_train_freezes():
    training = identity_rows(days=20, first=date(2026, 7, 22))
    frozen_train = experiment.advance_manifest(
        manifest(),
        training,
        finalized_slate_dates={row["slateDateEt"] for row in training},
    )
    assert frozen_train["partitions"]["train"]["endSlateDate"] == "2026-08-10"
    late_older = identity_rows(days=1, first=date(2026, 7, 21))

    updated = experiment.advance_manifest(
        frozen_train,
        training + late_older,
        finalized_slate_dates={row["slateDateEt"] for row in training + late_older},
    )

    assert updated["partitions"]["validation"]["rowCount"] == 0
    assert updated["historicalDiagnosticSlateDates"]["2026-07-21"]["reason"] == (
        "not_strictly_after_frozen_training_partition"
    )


def test_selection_ledger_binds_pre_outcome_decision_after_cutover():
    _, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-01T00:00:00+00:00"
    )
    row = identity_rows(
        days=1,
        games_per_day=1,
        first=date(2026, 9, 2),
    )[0]
    row.pop("labelRetrievedAtUtc")
    entry = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:00:00+00:00",
    )

    assert entry["selected"] is True
    assert entry["idempotencyFingerprintVersion"] == (
        experiment.SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V2
    )
    assert entry["outcomeKnownAtCapture"] is False
    assert entry["prospectiveCutoverAtUtc"] == "2026-09-01T00:00:00+00:00"
    assert experiment.selection_ledger_validation_errors(
        entry,
        bound,
        row=row,
        challenger_artifact_digest="a" * 64,
    ) == []

    retry = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:05:00+00:00",
    )
    assert retry["capturedAtUtc"] != entry["capturedAtUtc"]
    assert retry["idempotencyFingerprint"] == entry["idempotencyFingerprint"]
    assert retry["decisionFingerprint"] != entry["decisionFingerprint"]
    assert retry["recordFingerprint"] != entry["recordFingerprint"]

    tampered = copy.deepcopy(entry)
    tampered["selected"] = False
    errors = experiment.selection_ledger_validation_errors(
        tampered,
        bound,
        row=row,
        challenger_artifact_digest="a" * 64,
    )
    assert "selection_decision_mismatch" in errors
    assert "selection_decision_fingerprint_mismatch" in errors
    assert "selection_record_fingerprint_mismatch" in errors

    capture_tamper = copy.deepcopy(entry)
    capture_tamper["capturedAtUtc"] = "2026-09-02T20:01:00+00:00"
    capture_errors = experiment.selection_ledger_validation_errors(
        capture_tamper,
        bound,
        row=row,
        challenger_artifact_digest="a" * 64,
    )
    assert "selection_record_fingerprint_mismatch" in capture_errors
    assert "selection_decision_fingerprint_mismatch" in capture_errors


def test_selection_v2_idempotency_is_stable_across_deployment_capture_and_manifest():
    _, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-01T00:00:00+00:00"
    )
    row = identity_rows(
        days=1,
        games_per_day=1,
        first=date(2026, 9, 2),
    )[0]
    row.pop("labelRetrievedAtUtc")
    first = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:00:00+00:00",
    )

    revised_manifest = copy.deepcopy(bound)
    revised_manifest["updatedAtUtc"] = "2026-09-02T19:00:00+00:00"
    revised_manifest["manifestDigest"] = experiment.manifest_digest(revised_manifest)
    revised_deployment = {
        "gitSha": "c" * 40,
        "templateSha256": "d" * 64,
    }
    deployment_only_retry = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=revised_deployment,
        captured_at_utc="2026-09-02T20:00:00+00:00",
    )
    retry = experiment.selection_ledger_entry(
        revised_manifest,
        row,
        reliability_probability=0.7,
        deployment_identity=revised_deployment,
        captured_at_utc="2026-09-02T20:05:00+00:00",
    )

    assert retry["experimentManifestDigest"] != first["experimentManifestDigest"]
    assert retry["deploymentIdentity"] != first["deploymentIdentity"]
    assert retry["capturedAtUtc"] != first["capturedAtUtc"]
    assert retry["idempotencyFingerprint"] == first["idempotencyFingerprint"]
    assert deployment_only_retry["idempotencyFingerprint"] == (
        first["idempotencyFingerprint"]
    )
    assert experiment.selection_semantic_fingerprint(first) == first[
        "idempotencyFingerprint"
    ]
    assert experiment.selection_semantic_fingerprint(retry) == (
        experiment.selection_semantic_fingerprint(first)
    )
    assert deployment_only_retry["decisionFingerprint"] != first[
        "decisionFingerprint"
    ]
    assert deployment_only_retry["recordFingerprint"] != first[
        "recordFingerprint"
    ]
    assert retry["decisionFingerprint"] != first["decisionFingerprint"]
    assert retry["recordFingerprint"] != first["recordFingerprint"]


def test_selection_legacy_v1_fingerprint_still_validates_and_compares_semantically():
    _, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-01T00:00:00+00:00"
    )
    row = identity_rows(
        days=1,
        games_per_day=1,
        first=date(2026, 9, 2),
    )[0]
    row.pop("labelRetrievedAtUtc")
    current = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:00:00+00:00",
    )
    legacy = copy.deepcopy(current)
    legacy["idempotencyFingerprintVersion"] = (
        experiment.SELECTION_IDEMPOTENCY_FINGERPRINT_VERSION_V1
    )
    legacy["idempotencyFingerprint"] = experiment.selection_idempotency_fingerprint(
        legacy
    )
    legacy["decisionFingerprint"] = experiment.selection_decision_fingerprint(legacy)
    legacy["recordFingerprint"] = experiment.selection_record_fingerprint(legacy)

    assert legacy["idempotencyFingerprint"] == (
        "bff12a0259f7bdfc42e1a7be86521a6a4185327bda7bf950c5f2a4702dd6eaa3"
    )
    assert legacy["decisionFingerprint"] == (
        "a2ac86f2c5e2ba0402187d21813443f2d565a494b25d1e12b64c441f801c4bfd"
    )
    assert legacy["recordFingerprint"] == (
        "437a8c78a0bc1522a5e655ce30bc63e2e1be03f6dcfb294071d660165020f432"
    )
    assert experiment.selection_ledger_validation_errors(
        legacy,
        bound,
        row=row,
        challenger_artifact_digest="a" * 64,
    ) == []
    assert experiment.selection_semantic_fingerprint(legacy) == (
        current["idempotencyFingerprint"]
    )
    assert legacy["idempotencyFingerprint"] != current["idempotencyFingerprint"]

    tampered_legacy = copy.deepcopy(legacy)
    tampered_legacy["deploymentIdentity"]["gitSha"] = "e" * 40
    tamper_errors = experiment.selection_ledger_validation_errors(
        tampered_legacy,
        bound,
        row=row,
        challenger_artifact_digest="a" * 64,
    )
    assert "selection_idempotency_fingerprint_mismatch" in tamper_errors
    assert "selection_decision_fingerprint_mismatch" in tamper_errors
    assert "selection_record_fingerprint_mismatch" in tamper_errors


def test_selection_v2_material_decision_change_is_not_idempotent():
    _, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-01T00:00:00+00:00"
    )
    row = identity_rows(
        days=1,
        games_per_day=1,
        first=date(2026, 9, 2),
    )[0]
    row.pop("labelRetrievedAtUtc")
    first = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.7,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:00:00+00:00",
    )
    changed = experiment.selection_ledger_entry(
        bound,
        row,
        reliability_probability=0.71,
        deployment_identity=DEPLOYMENT_IDENTITY,
        captured_at_utc="2026-09-02T20:05:00+00:00",
    )

    assert changed["selected"] is first["selected"] is True
    assert changed["idempotencyFingerprint"] != first["idempotencyFingerprint"]
    assert experiment.selection_semantic_fingerprint(changed) != (
        experiment.selection_semantic_fingerprint(first)
    )


def test_selection_ledger_rejects_pre_cutover_game_or_capture():
    _, bound = _manifest_with_bound_challenger(
        bound_at_utc="2026-09-02T22:00:00+00:00"
    )
    row = identity_rows(
        days=1,
        games_per_day=1,
        first=date(2026, 9, 2),
    )[0]
    row.pop("labelRetrievedAtUtc")

    with pytest.raises(experiment.ExperimentContractError, match="after challenger cutover"):
        experiment.selection_ledger_entry(
            bound,
            row,
            reliability_probability=0.7,
            deployment_identity=DEPLOYMENT_IDENTITY,
            captured_at_utc="2026-09-02T21:59:59+00:00",
        )

    row["commenceTime"] = "2026-09-02T21:00:00+00:00"
    with pytest.raises(
        experiment.ExperimentContractError,
        match="strictly after cutover",
    ):
        experiment.selection_ledger_entry(
            bound,
            row,
            reliability_probability=0.7,
            deployment_identity=DEPLOYMENT_IDENTITY,
            captured_at_utc="2026-09-02T22:30:00+00:00",
        )


def test_frozen_slate_membership_or_vector_change_fails_closed():
    rows = identity_rows(days=20)
    frozen = experiment.advance_manifest(
        manifest(),
        rows,
        finalized_slate_dates={row["slateDateEt"] for row in rows},
    )
    changed = copy.deepcopy(rows)
    changed[0]["featureSnapshot"]["fingerprint"] = "changed-after-freeze"
    with pytest.raises(experiment.FrozenPartitionConflict):
        experiment.advance_manifest(
            frozen,
            changed,
            finalized_slate_dates={row["slateDateEt"] for row in changed},
        )


def test_v2_admission_requires_exact_snapshot_ref_and_persistence_ack():
    row = valid_admission_row()
    ok, reasons = experiment.validate_record(row, manifest())
    assert ok is True, reasons
    assert reasons == []

    missing_ref = copy.deepcopy(row)
    missing_ref.pop("fundamentalsSnapshotRefV2")
    assert "missing_fundamentals_v2_snapshot_reference" in experiment.validate_record(
        missing_ref, manifest()
    )[1]

    inferred_time = copy.deepcopy(row)
    inferred_time.pop("predictionPersistedAtUtc")
    inferred_time["createdAt"] = "2026-07-21T12:05:00+00:00"
    assert "immutable_prediction_persistence_time_missing" in experiment.validate_record(
        inferred_time, manifest()
    )[1]


def test_legacy_vector_and_pre_release_rows_never_enter_new_cohort():
    row = valid_admission_row()
    legacy = copy.deepcopy(row)
    legacy["featureSnapshot"]["version"] = "MLB-VECTOR-v1"
    assert "wrong_or_legacy_feature_vector_version" in experiment.validate_record(
        legacy, manifest()
    )[1]

    pre_release = copy.deepcopy(row)
    pre_release["featureSnapshot"]["lockAtUtc"] = (
        datetime(2026, 7, 20, 23, 59, tzinfo=timezone.utc).isoformat()
    )
    assert "pre_release_or_missing_lock_timestamp" in experiment.validate_record(
        pre_release, manifest()
    )[1]


def test_r2_release_cutoff_rejects_backfill_and_accepts_exact_boundary():
    r2 = experiment.new_manifest(
        experiment_id="mlb-v2-r2-cutoff-test",
        release_contract_id="mlb-v2-2026-07-22-future-prospective-r3",
        release_cutoff_utc="2026-07-22T04:00:00+00:00",
        feature_vector_version="MLB-VECTOR-v2",
        model_feature_schemas=FEATURE_SCHEMAS,
        created_at_utc="2026-07-22T04:00:00+00:00",
    )
    base = valid_admission_row()

    july_20 = copy.deepcopy(base)
    july_20["slateDateEt"] = "2026-07-20"
    july_20["featureSnapshot"]["lockAtUtc"] = "2026-07-20T23:00:00+00:00"
    assert "pre_release_or_missing_lock_timestamp" in experiment.validate_record(
        july_20, r2
    )[1]

    one_second_early = copy.deepcopy(base)
    one_second_early["featureSnapshot"]["lockAtUtc"] = "2026-07-21T19:29:59+00:00"
    assert "pre_release_or_missing_lock_timestamp" in experiment.validate_record(
        one_second_early, r2
    )[1]

    at_boundary = copy.deepcopy(base)
    at_boundary["featureSnapshot"]["lockAtUtc"] = "2026-07-22T04:00:00+00:00"
    ok, reasons = experiment.validate_record(at_boundary, r2)
    assert ok is True, reasons


def test_feature_limit_is_enforced_per_model_not_on_union():
    created = manifest()
    assert len(created["modelFeatureSchemas"]["outcome"]) == 8
    assert len(created["modelFeatureSchemas"]["reliability"]) == 10
    assert len(created["featureNames"]) == 18
    with pytest.raises(experiment.ExperimentContractError):
        experiment.new_manifest(
            experiment_id="bad",
            release_contract_id="release-contract-r1",
            release_cutoff_utc="2026-07-21T00:00:00+00:00",
            feature_vector_version="v2",
            model_feature_schemas={"outcome": [f"x{i}" for i in range(11)]},
        )


def test_generic_manifest_may_omit_release_activation() -> None:
    assert "releaseActivation" not in manifest()


def test_release_activation_is_validated_and_bound_into_manifest_digest() -> None:
    activated_at = "2026-07-21T00:00:00+00:00"
    cutoff = "2026-07-21T01:00:00+00:00"
    activation = experiment.release_activation(
        experiment_id="mlb-v2-activation-test",
        release_contract_id="release-activation-test",
        release_cutoff_utc=cutoff,
        activated_at_utc=activated_at,
        deployment_git_sha="a" * 40,
        deployment_template_sha256="b" * 64,
    )
    created = experiment.new_manifest(
        experiment_id="mlb-v2-activation-test",
        release_contract_id="release-activation-test",
        release_cutoff_utc=cutoff,
        feature_vector_version="MLB-VECTOR-v2",
        model_feature_schemas=FEATURE_SCHEMAS,
        created_at_utc=activated_at,
        release_activation=activation,
    )

    assert created["releaseActivation"] == activation
    assert created["manifestDigest"] == experiment.manifest_digest(created)
    original_digest = created["manifestDigest"]
    created["releaseActivation"]["deploymentIdentity"]["gitSha"] = "c" * 40
    assert experiment.manifest_digest(created) != original_digest


def test_release_activation_rejects_exact_cutoff_and_activation_before_creation() -> None:
    cutoff = "2026-07-22T04:00:00+00:00"
    with pytest.raises(
        experiment.ExperimentContractError,
        match="strictly before the release cutoff",
    ):
        experiment.release_activation(
            experiment_id="mlb-v2-activation-test",
            release_contract_id="release-activation-test",
            release_cutoff_utc=cutoff,
            activated_at_utc=cutoff,
            deployment_git_sha="a" * 40,
            deployment_template_sha256="b" * 64,
        )

    activation = experiment.release_activation(
        experiment_id="mlb-v2-activation-test",
        release_contract_id="release-activation-test",
        release_cutoff_utc=cutoff,
        activated_at_utc="2026-07-22T03:59:58+00:00",
        deployment_git_sha="a" * 40,
        deployment_template_sha256="b" * 64,
    )
    with pytest.raises(
        experiment.ExperimentContractError,
        match="release_activation_predates_manifest_creation",
    ):
        experiment.new_manifest(
            experiment_id="mlb-v2-activation-test",
            release_contract_id="release-activation-test",
            release_cutoff_utc=cutoff,
            feature_vector_version="MLB-VECTOR-v2",
            model_feature_schemas=FEATURE_SCHEMAS,
            created_at_utc="2026-07-22T03:59:59+00:00",
            release_activation=activation,
        )


def test_first_full_clean_slate_milestone_requires_canonical_slate_authority():
    status = experiment.milestone_status(
        manifest(),
        integrity_clean_row_count=15,
        settled_selected_recommendation_count=0,
    )

    assert status["targets"]["firstFullCleanSlateProof"] == 15
    assert status["counts"]["completedFinalizedSlates"] == 0
    proof = status["firstFullCleanSlateProof"]
    assert proof["state"] == "WAITING_FOR_FIRST_COMPLETE_FINALIZED_SLATE"
    assert proof["achieved"] is False
    assert proof["currentCleanGames"] == 0
    assert proof["globalIntegrityCleanGames"] == 15
    assert proof["remainingCleanGames"] == 15
    assert proof["qualifyingSlateDate"] is None
    assert proof["evaluatedSlateProofs"] == []
    assert proof["exactOfficialGameSetEqualityRequired"] is True


def test_first_full_clean_slate_milestone_records_count_and_achievement():
    rows = full_clean_slate_rows(game_count=3)
    updated = experiment.advance_manifest(
        manifest(),
        rows,
        finalized_slate_dates={"2026-07-21"},
        updated_at_utc="2026-07-22T05:00:00+00:00",
    )
    authority = official_slate_authority(rows)

    status = experiment.milestone_status(
        updated,
        integrity_clean_row_count=len(rows),
        settled_selected_recommendation_count=0,
        integrity_clean_rows=rows,
        official_finalized_slate_authorities={"2026-07-21": authority},
    )

    assert status["counts"]["completedFinalizedSlates"] == 1
    assert status["remainingRows"]["firstFullCleanSlateProof"] == 0
    assert status["projectedFullCleanSlatesRemaining"][
        "firstFullCleanSlateProof"
    ] == 0
    assert status["firstFullCleanSlateProof"] == {
        "state": "FIRST_FULL_CLEAN_SLATE_PROOF_ACHIEVED",
        "achieved": True,
        "targetCleanGames": 3,
        "planningEstimateCleanGames": 15,
        "currentCleanGames": 3,
        "globalIntegrityCleanGames": 3,
        "remainingCleanGames": 0,
        "completedFinalizedSlateCount": 1,
        "completedFinalizedSlateDates": ["2026-07-21"],
        "qualifyingSlateDate": "2026-07-21",
        "qualifyingOfficialGameSetFingerprint": authority[
            "officialGameSetFingerprint"
        ],
        "exactOfficialGameSetEqualityRequired": True,
        "evaluatedSlateProofs": [
            {
                "slateDateEt": "2026-07-21",
                "achieved": True,
                "officialGameCount": 3,
                "cleanEligibleGameCount": 3,
                "missingOfficialGamePks": [],
                "unexpectedCleanGamePks": [],
                "duplicateCleanOfficialGamePks": [],
                "officialGameSetFingerprint": authority[
                    "officialGameSetFingerprint"
                ],
                "cleanGameSetFingerprint": authority[
                    "officialGameSetFingerprint"
                ],
                "cleanSlateFingerprint": experiment.slate_fingerprint(rows),
                "authorityFingerprint": authority["authorityFingerprint"],
                "errors": [],
            }
        ],
        "authority": (
            "one nonempty verified official FINAL gamePk set must exactly equal "
            "the same immutable slate's unique, current, post-cutoff clean "
            "eligible gamePk set"
        ),
    }


def test_first_full_clean_slate_does_not_combine_clean_rows_across_dates():
    first = full_clean_slate_rows(game_count=8, slate="2026-07-21")
    second = full_clean_slate_rows(game_count=7, slate="2026-07-22")
    rows = first + second
    updated = experiment.advance_manifest(
        manifest(),
        rows,
        finalized_slate_dates={"2026-07-21", "2026-07-22"},
    )
    authorities = {
        "2026-07-21": official_slate_authority(first, "2026072199"),
        "2026-07-22": official_slate_authority(second, "2026072299"),
    }

    status = experiment.milestone_status(
        updated,
        integrity_clean_row_count=15,
        settled_selected_recommendation_count=0,
        integrity_clean_rows=rows,
        official_finalized_slate_authorities=authorities,
    )

    proof = status["firstFullCleanSlateProof"]
    assert proof["achieved"] is False
    assert proof["state"] == "COLLECTING_FIRST_EXACT_FULL_CLEAN_SLATE"
    assert proof["currentCleanGames"] == 8
    assert proof["remainingCleanGames"] == 1
    assert all(
        item["errors"] == ["official_game_set_missing_clean_rows"]
        for item in proof["evaluatedSlateProofs"]
    )


@pytest.mark.parametrize(
    "failure", ["missing", "duplicate", "tampered", "pre_cutoff", "not_finalized"]
)
def test_first_full_clean_slate_proof_fails_closed_on_bad_same_slate_rows(failure):
    original = full_clean_slate_rows(game_count=3)
    updated = experiment.advance_manifest(
        manifest(),
        original,
        finalized_slate_dates={"2026-07-21"},
    )
    authority = official_slate_authority(original)
    observed = copy.deepcopy(original)
    if failure == "missing":
        observed.pop()
    elif failure == "duplicate":
        observed[1]["officialGamePk"] = observed[0]["officialGamePk"]
    elif failure == "tampered":
        observed[0]["featureSnapshot"]["fingerprint"] = "tampered-vector"
    elif failure == "pre_cutoff":
        observed[0]["featureSnapshot"]["lockAtUtc"] = "2026-07-20T23:59:59+00:00"
    else:
        observed[0]["slateFinalized"] = False

    status = experiment.milestone_status(
        updated,
        integrity_clean_row_count=len(observed),
        settled_selected_recommendation_count=0,
        integrity_clean_rows=observed,
        official_finalized_slate_authorities={"2026-07-21": authority},
    )

    slate_proof = status["firstFullCleanSlateProof"]["evaluatedSlateProofs"][0]
    assert status["firstFullCleanSlateProof"]["achieved"] is False
    assert slate_proof["achieved"] is False
    if failure == "duplicate":
        assert "duplicate_clean_official_game_pk" in slate_proof["errors"]
    elif failure in {"missing", "tampered"}:
        assert "immutable_manifest_slate_fingerprint_mismatch" in slate_proof[
            "errors"
        ]
    elif failure == "pre_cutoff":
        assert "clean_row_not_after_release_cutoff" in slate_proof["errors"]
    else:
        assert "clean_row_slate_finalized_proof_missing" in slate_proof["errors"]


def test_first_full_clean_slate_rejects_tampered_official_set_fingerprint():
    rows = full_clean_slate_rows(game_count=2)
    updated = experiment.advance_manifest(
        manifest(), rows, finalized_slate_dates={"2026-07-21"}
    )
    authority = official_slate_authority(rows)
    authority["officialGamePks"].append("tampered-game-pk")

    status = experiment.milestone_status(
        updated,
        integrity_clean_row_count=2,
        settled_selected_recommendation_count=0,
        integrity_clean_rows=rows,
        official_finalized_slate_authorities={"2026-07-21": authority},
    )

    proof = status["firstFullCleanSlateProof"]["evaluatedSlateProofs"][0]
    assert proof["achieved"] is False
    assert "official_game_set_fingerprint_mismatch" in proof["errors"]
    assert "official_finalized_slate_authority_fingerprint_mismatch" in proof[
        "errors"
    ]
