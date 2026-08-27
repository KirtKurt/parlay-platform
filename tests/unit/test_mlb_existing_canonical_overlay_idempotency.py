from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_locked_prediction_storage_finalizer_v1 as finalizer
import mlb_manual_pull_protected as protected_pull
import mlb_slate_coverage_patch as coverage
from scripts.verify_mlb_immutable_locked_storage import (
    FakeHistory,
    locked_row,
    seed_stage,
)


SLATE = "2026-08-27"


def _canonical_public_overlay(row):
    # Exercise the exact production helpers and constants rather than
    # hand-building a lookalike marker set.
    public_lock = dict(row.get("slatePredictionLock") or {})
    public_lock.update(
        {
            "authorityVersion": coverage.AUTHORITY_VERSION,
            "canonicalReadOperational": True,
            "perGameLock": True,
            "slateWideLock": False,
            "lockStatus": "PARTIAL_PER_GAME_CANONICAL",
            "pendingCanonicalGameCount": 5,
        }
    )
    out = coverage._official_row(row, public_lock)
    out = coverage._overlay_playability(
        out,
        {
            "playable": False,
            "status": "BLOCKED",
            "reasons": ["NEGATIVE_EV_GUARD"],
            "validationErrors": [],
            "historicalValidationErrors": [],
            "assessment": None,
            "requiredCheckpoint": None,
            "requiredCheckpointDue": False,
            "eventPendingRequired": False,
        },
    )
    out = coverage._overlay_readiness(
        out,
        {
            "checkpoints": {
                "tMinus50": {
                    "recorded": True,
                    "status": "READY",
                }
            },
            "requiredCheckpoint": None,
            "requiredCheckpointDue": False,
            "validationErrors": [],
        },
    )
    out["slateCoverageVersion"] = coverage.VERSION
    return out


def _assert_live_overlay_markers(row):
    authority = row["canonicalPerGameStageAuthority"]
    per_game = row["perGameCanonicalLock"]
    public_lock = row["slatePredictionLock"]
    gate = row["lastPossiblePredictionGate"]
    assert row["officialPredictionReason"] == (
        "validated_immutable_canonical_per_game_lock"
    )
    assert row["slateCoverageVersion"] == coverage.VERSION
    assert row["immutablePerGameStage"] is True
    assert row["immutableLockedStorage"] is True
    assert row["immutableLockedStorageKeyspace"] == "LOCKED#GAME"
    assert row["immutableLockedStorageVersion"] == immutable_storage.VERSION
    assert row["canonical"] is True
    assert row["locked"] is True
    assert row["lockedPrediction"] is True
    assert row["officialPrediction"] is True
    assert row["officialPick"] is True
    assert row["isOfficialDisplayPick"] is True
    assert row["lockStatus"] == "LOCKED_CANONICAL"
    assert row["officialPredictionStatus"] == (
        "OFFICIAL_LOCKED_PREDICTION"
    )
    assert row["selectionFingerprint"] == (
        row["lastPrelockSelectionFingerprint"]
    )
    assert authority["version"] == immutable_storage.AUTHORITY_VERSION
    assert authority["verified"] is True
    assert authority["consistentRead"] is True
    assert per_game == {
        "authorityVersion": coverage.AUTHORITY_VERSION,
        "status": "OFFICIAL_LOCKED_PREDICTION",
        "lockAtUtc": row["lockedAtUtc"],
        "canonical": True,
    }
    assert public_lock["policyVersion"] == coverage.AUTHORITY_VERSION
    assert public_lock["authorityVersion"] == coverage.AUTHORITY_VERSION
    assert public_lock["canonicalReadOperational"] is True
    assert public_lock["perGameLock"] is True
    assert public_lock["slateWideLock"] is False
    assert public_lock["locked"] is True
    assert public_lock["lockStatus"] == "OFFICIAL_LOCKED_PREDICTION"
    assert public_lock["lockAtUtc"] == row["lockedAtUtc"]
    assert gate["policyVersion"] == coverage.AUTHORITY_VERSION
    assert gate["phase"] == "FINAL_LOCKED"
    assert gate["finalWindowActive"] is False
    assert gate["finalLocked"] is True
    assert gate["perGameLock"] is True
    assert gate["slateWideLock"] is False
    assert gate["lockAtUtc"] == row["lockedAtUtc"]
    assert immutable_storage._canonical_read_overlay(row) is True


def _module_with_existing_canonical_rows(count, *, vector_excluded=False):
    history = FakeHistory()
    mutable_store_calls = []

    def original_store(row):
        mutable_store_calls.append(copy.deepcopy(row))
        return {
            "ok": True,
            "pk": f"GAME_WINNERS#mlb#{SLATE}",
            "sk": (
                f"GAME#{row.get('commenceTime')}#"
                f"{row.get('gameIdentity') or row.get('gameId')}"
            ),
        }

    module = SimpleNamespace(
        history=history,
        _store_prediction=original_store,
    )
    immutable_storage.apply(module)

    rows = []
    for index in range(count):
        hour = 17 + index
        base = {
            "slate_date": SLATE,
            "gameId": f"canonical-{index}",
            "gameIdentity": f"canonical-{index}",
            "commenceTime": f"{SLATE}T{hour:02d}:05:00+00:00",
            "predictedWinner": "Home Team",
            "createdAt": f"{SLATE}T{hour - 2:02d}:00:00+00:00",
        }
        row = locked_row(
            base,
            game_id=f"canonical-{index}",
        )
        if vector_excluded:
            row["frozenFeatureVector"]["fingerprint"] = (
                f"invalid-vector-{index}"
            )
            row = vector_contract.apply_exact_vector_training_status(row)
            assert row["exactVectorVerified"] is False
            assert row["trainingEligible"] is False
            assert row["exactVectorValidationErrors"]
        seed_stage(history, row)
        created = module._store_prediction(row)
        assert created["ok"] is True
        assert created["created"] is True
        rows.append(row)
    mutable_store_calls.clear()
    return module, rows, mutable_store_calls


def _arm_zero_write_guard(table):
    calls = []

    def reject_write(*args, **kwargs):
        calls.append(
            {
                "args": copy.deepcopy(args),
                "kwargs": copy.deepcopy(kwargs),
            }
        )
        raise AssertionError(
            "canonical overlay verification attempted a write"
        )

    table.put_item = reject_write
    table.update_item = reject_write
    table.transact_write_items = reject_write
    return calls


def _arm_read_tracker(table):
    calls = []
    original_get_item = table.get_item

    def tracked_get_item(*args, **kwargs):
        calls.append(
            {
                "Key": copy.deepcopy(kwargs.get("Key")),
                "ConsistentRead": kwargs.get("ConsistentRead"),
            }
        )
        return original_get_item(*args, **kwargs)

    table.get_item = tracked_get_item
    return calls


def _assert_strong_read(read_calls, key):
    assert {
        "Key": key,
        "ConsistentRead": True,
    } in read_calls


def _scheduled_response(result):
    scheduled_result = copy.deepcopy(result)
    scheduled_result.update(
        {
            "game_date_et": SLATE,
            "gameCount": 7,
            "preLockStorageLifecycleAware": True,
            "displayStatusCoverageComplete": True,
            "lifecycleCoverageComplete": True,
            "allGamesPredicted": False,
            "operationalDefectScopeVersion": (
                protected_pull._WINNER_LIFECYCLE_DEFECT_SCOPE_VERSION
            ),
            "winnerLifecycleOperationalDefect": False,
            "releasePlayabilityOperationalDefect": False,
            "operationalDefectScopes": [],
            "operationalDefect": False,
        }
    )
    payload = {
        "ok": True,
        "count": 7,
        "providerScheduleManifestComplete": True,
        "provider_schedule_manifests": [
            {
                "ok": True,
                "immutable": True,
                "fullProviderSchedule": True,
                "boundToCanonicalPull": True,
                "version": "test-provider-manifest-v1",
                "fingerprint": "test-provider-manifest-fingerprint",
                "pk": f"PROVIDER_SCHEDULE#mlb#{SLATE}",
                "sk": "MANIFEST#test",
                "game_date_et": SLATE,
                "gameCount": 7,
            }
        ],
        "game_winner_predictions": [scheduled_result],
    }
    return {
        "statusCode": 200,
        "body": json.dumps(payload),
    }


def _assert_rejected_without_write(module, row, before, write_calls):
    rejected = module._store_prediction(row)
    assert rejected["ok"] is False
    assert rejected["canonicalWriteAttempted"] is False
    assert rejected["canonicalReadOverlayVerified"] is False
    assert module.history.PULLS.items == before
    assert write_calls == []
    return rejected


def test_mixed_slate_two_existing_canonical_and_five_future_rows_is_healthy():
    module, canonical_rows, mutable_store_calls = (
        _module_with_existing_canonical_rows(2)
    )
    overlays = [
        _canonical_public_overlay(row)
        for row in canonical_rows
    ]
    for overlay in overlays:
        _assert_live_overlay_markers(overlay)
    future_rows = [
        {
            "slate_date": SLATE,
            "gameId": f"future-{index}",
            "gameIdentity": f"future-{index}",
            "commenceTime": f"{SLATE}T23:{index:02d}:00+00:00",
            "homeTeam": "Future Home",
            "awayTeam": "Future Away",
            "predictedWinner": "Future Home",
            "predictedSide": "home",
            "lockedPrediction": False,
            "officialPrediction": False,
            "officialPredictionStatus": (
                "PRE_LOCK_PLATFORM_PREDICTION"
            ),
            "lockStatus": "OPEN_PRE_LOCK",
            "perGameCanonicalLock": {
                "authorityVersion": coverage.AUTHORITY_VERSION,
                "status": "OPEN_PRE_LOCK",
                "canonical": False,
            },
            "tags": ["PRE_LOCK_PREDICTION"],
        }
        for index in range(5)
    ]
    source_result = {
        "ok": True,
        "sport": "mlb",
        "slate_date": SLATE,
        "gameCount": 7,
        "count": 7,
        "predictions": overlays + future_rows,
    }
    module.predict_all = (
        lambda *args, **kwargs: copy.deepcopy(source_result)
    )
    finalizer.apply(module)

    table = module.history.PULLS
    read_calls = _arm_read_tracker(table)
    write_calls = _arm_zero_write_guard(table)
    before = copy.deepcopy(table.items)
    result = module.predict_all(SLATE, store=True)
    after = copy.deepcopy(table.items)

    assert result["canonicalLockedStorageCandidateCount"] == 2
    assert result["canonicalLockedStoredCount"] == 2
    assert result["canonicalLockedStorageErrors"] == {}
    assert result["canonicalLockedStorageComplete"] is True
    assert result["preLockStorageCandidateCount"] == 5
    assert result["preLockStoredCount"] == 5
    assert result["preLockStorageComplete"] is True
    assert result["preLockStorageDispositionCount"] == 7
    assert result["preLockStorageDispositionComplete"] is True
    assert result["storedCount"] == 7
    assert result["ok"] is True
    assert len(mutable_store_calls) == 5
    assert before == after
    assert write_calls == []
    for row in canonical_rows:
        _assert_strong_read(
            read_calls,
            immutable_storage._locked_key(row),
        )
        _assert_strong_read(
            read_calls,
            immutable_storage._stage_key(row),
        )

    canonical_results = [
        row["canonicalLockedStore"]
        for row in result["predictions"][:2]
    ]
    assert all(
        stored["immutableExisting"] is True
        and stored["idempotentExistingVerified"] is True
        and stored["canonicalReadOverlayVerified"] is True
        and stored["canonicalWriteAttempted"] is False
        and stored["created"] is False
        for stored in canonical_results
    )

    # Exercise the unchanged scheduled alarm with the exact mixed result.
    scheduled_response = _scheduled_response(result)
    protected_pull._raise_scheduled_delegate_failure(
        {},
        scheduled_response,
    )

    # The same alarm must still fail closed on a true storage mismatch.
    broken_response = copy.deepcopy(scheduled_response)
    broken_payload = json.loads(broken_response["body"])
    broken_result = broken_payload["game_winner_predictions"][0]
    broken_result["canonicalLockedStoredCount"] = 1
    broken_result["canonicalLockedStorageComplete"] = False
    broken_response["body"] = json.dumps(broken_payload)
    with pytest.raises(RuntimeError) as excinfo:
        protected_pull._raise_scheduled_delegate_failure(
            {},
            broken_response,
        )
    assert "canonical_locked_storage_incomplete" in str(excinfo.value)
    assert "canonical_locked_storage_count_mismatch" in str(
        excinfo.value
    )


def test_existing_training_ineligible_selection_remains_idempotent():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(
        1,
        vector_excluded=True,
    )
    overlay = _canonical_public_overlay(canonical_rows[0])
    _assert_live_overlay_markers(overlay)
    table = module.history.PULLS
    read_calls = _arm_read_tracker(table)
    write_calls = _arm_zero_write_guard(table)
    before = copy.deepcopy(table.items)

    verified = module._store_prediction(overlay)

    assert verified["ok"] is True
    assert verified["immutableExisting"] is True
    assert verified["idempotentExistingVerified"] is True
    assert verified["canonicalWriteAttempted"] is False
    assert verified["canonicalReadOverlayVerified"] is True
    assert verified["exactVectorVerified"] is False
    assert verified["exactVectorValidationErrors"]
    assert verified["incomingExactVectorValidationErrors"] == (
        verified["exactVectorValidationErrors"]
    )
    assert verified["trainingEligible"] is False
    assert verified["trainingExclusionReasons"]
    assert table.items == before
    assert write_calls == []
    _assert_strong_read(
        read_calls,
        immutable_storage._locked_key(overlay),
    )
    _assert_strong_read(
        read_calls,
        immutable_storage._stage_key(overlay),
    )


def test_existing_overlay_immutable_tampering_still_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    _assert_live_overlay_markers(overlay)
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    wrong_side = copy.deepcopy(overlay)
    wrong_side["predictedWinner"] = "Away Team"
    wrong_side["predictedSide"] = "away"
    rejected_side = _assert_rejected_without_write(
        module,
        wrong_side,
        before,
        write_calls,
    )
    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected_side["authorityErrors"]
    )

    wrong_stage = copy.deepcopy(overlay)
    wrong_stage["canonicalPerGameStageAuthority"][
        "stageFingerprint"
    ] = "tampered-stage-fingerprint"
    rejected_stage = _assert_rejected_without_write(
        module,
        wrong_stage,
        before,
        write_calls,
    )
    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected_stage["authorityErrors"]
    )

    wrong_vector = copy.deepcopy(overlay)
    wrong_vector["frozenFeatureVector"]["fingerprint"] = (
        "tampered-vector-fingerprint"
    )
    rejected_vector = _assert_rejected_without_write(
        module,
        wrong_vector,
        before,
        write_calls,
    )
    assert any(
        "vector" in error
        for error in rejected_vector["authorityErrors"]
    )


def test_existing_overlay_first_read_failure_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    def fail_read(*args, **kwargs):
        raise RuntimeError("injected consistent-read failure")

    table.get_item = fail_read
    read_failure = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )
    assert any(
        error.startswith(
            "canonical_overlay_consistent_read_failed:RuntimeError:"
        )
        for error in read_failure["authorityErrors"]
    )


def test_existing_overlay_missing_locked_item_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    key = immutable_storage._locked_key(overlay)
    table.items.pop((key["PK"], key["SK"]))
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    missing = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert missing["authorityErrors"] == [
        "canonical_overlay_existing_locked_item_missing"
    ]


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("predicted_winner", "Away Team"),
        ("game_identity", "same-key-tampered-identity"),
        ("stage_fingerprint", "tampered-stage-fingerprint"),
        ("exact_vector_verified", False),
        ("training_eligible", False),
    ],
)
def test_existing_locked_envelope_tamper_fails_closed_without_write(
    field,
    tampered,
):
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    key = immutable_storage._locked_key(overlay)
    table.items[(key["PK"], key["SK"])][field] = tampered
    before = copy.deepcopy(table.items)
    read_calls = _arm_read_tracker(table)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert any(
        error.startswith(
            "canonical_overlay_existing_envelope_mismatch:"
        )
        and field in error
        for error in rejected["authorityErrors"]
    )
    _assert_strong_read(read_calls, key)
    _assert_strong_read(
        read_calls,
        immutable_storage._stage_key(overlay),
    )


def test_existing_locked_data_tamper_fails_closed_without_write():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    key = immutable_storage._locked_key(overlay)
    table.items[(key["PK"], key["SK"])]["data"][
        "predictedWinner"
    ] = "Away Team"
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert any(
        error.startswith(
            "canonical_overlay_existing_envelope_mismatch:"
        )
        for error in rejected["authorityErrors"]
    )
    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected["authorityErrors"]
    )


def test_existing_overlay_stage_missing_fails_closed_without_write():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    stage_key = immutable_storage._stage_key(overlay)
    table.items.pop((stage_key["PK"], stage_key["SK"]))
    before = copy.deepcopy(table.items)
    read_calls = _arm_read_tracker(table)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert "canonical_overlay_verified_stage_not_found" in (
        rejected["authorityErrors"]
    )
    _assert_strong_read(
        read_calls,
        immutable_storage._locked_key(overlay),
    )
    _assert_strong_read(read_calls, stage_key)


def test_existing_overlay_stage_tamper_fails_closed_without_write():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    stage_key = immutable_storage._stage_key(overlay)
    table.items[(stage_key["PK"], stage_key["SK"])][
        "stage_fingerprint"
    ] = "tampered-stage-fingerprint"
    before = copy.deepcopy(table.items)
    read_calls = _arm_read_tracker(table)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert "stage_fingerprint_mismatch" in (
        rejected["authorityErrors"]
    )
    _assert_strong_read(
        read_calls,
        immutable_storage._locked_key(overlay),
    )
    _assert_strong_read(read_calls, stage_key)


def test_existing_overlay_stage_second_read_failure_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    locked_key = immutable_storage._locked_key(overlay)
    stage_key = immutable_storage._stage_key(overlay)
    original_get_item = table.get_item
    read_calls = []

    def fail_stage_read(*args, **kwargs):
        read_calls.append(
            {
                "Key": copy.deepcopy(kwargs.get("Key")),
                "ConsistentRead": kwargs.get("ConsistentRead"),
            }
        )
        if kwargs.get("Key") == stage_key:
            raise RuntimeError(
                "injected stage consistent-read failure"
            )
        return original_get_item(*args, **kwargs)

    table.get_item = fail_stage_read
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)
    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert any(
        error.startswith(
            "canonical_overlay_stage_consistent_read_failed:"
            "RuntimeError:"
        )
        for error in rejected["authorityErrors"]
    )
    _assert_strong_read(read_calls, locked_key)
    _assert_strong_read(read_calls, stage_key)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update({"locked": False}),
        lambda row: row.update({"officialPrediction": False}),
        lambda row: row.update({"isOfficialDisplayPick": False}),
        lambda row: row.update({"selectionFingerprint": "tampered"}),
        lambda row: row["perGameCanonicalLock"].update(
            {"status": "OPEN_PRE_LOCK"}
        ),
        lambda row: row["slatePredictionLock"].update(
            {"lockAtUtc": "2026-08-27T00:00:00+00:00"}
        ),
    ],
)
def test_inexact_public_overlay_marker_never_uses_idempotent_path(
    mutation,
):
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    mutation(overlay)
    assert immutable_storage._canonical_read_overlay(overlay) is False
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = module._store_prediction(overlay)

    assert rejected["ok"] is False
    assert table.items == before
    assert write_calls == []

@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update({"score": 999.0}),
        lambda row: row.update({"winProbability": 0.99}),
        lambda row: row.update({"confidenceTier": "TAMPERED"}),
        lambda row: row.update({"createdAt": "2026-08-27T00:00:00+00:00"}),
        lambda row: row.update(
            {"predictionSourcePullAt": "2026-08-27T00:00:00+00:00"}
        ),
        lambda row: row.update(
            {"lastPrelockPromotionVersion": "tampered-promotion"}
        ),
        lambda row: row["mlFeatureFreeze"].update(
            {"completeSlateCoverage": False}
        ),
    ],
)
def test_incoming_immutable_model_or_stage_field_tamper_fails_closed(
    mutation,
):
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    mutation(overlay)
    assert immutable_storage._canonical_read_overlay(overlay) is True
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected["authorityErrors"]
    )


def test_equal_invalid_vector_errors_do_not_hide_feature_tamper():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(
        1,
        vector_excluded=True,
    )
    overlay = _canonical_public_overlay(canonical_rows[0])
    tampered = copy.deepcopy(overlay)
    tampered["frozenFeatureVector"][
        "unvalidatedPayload"
    ] = "tampered"
    tampered = vector_contract.apply_exact_vector_training_status(
        tampered
    )
    existing_errors = immutable_storage._require_vector_status(
        canonical_rows[0],
        context="test_existing",
    )
    incoming_errors = immutable_storage._require_vector_status(
        tampered,
        context="test_incoming",
    )
    assert existing_errors
    assert incoming_errors == existing_errors
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        tampered,
        before,
        write_calls,
    )

    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected["authorityErrors"]
    )


def test_existing_locked_envelope_extra_field_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    key = immutable_storage._locked_key(overlay)
    table.items[(key["PK"], key["SK"])][
        "unexpected_outer_authority"
    ] = True
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert any(
        error.startswith(
            "canonical_overlay_existing_envelope_keyset_mismatch:"
        )
        and "unexpected_outer_authority" in error
        for error in rejected["authorityErrors"]
    )


def test_existing_stage_authority_extra_field_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    table = module.history.PULLS
    key = immutable_storage._locked_key(overlay)
    stored_row = table.items[(key["PK"], key["SK"])]["data"]
    stored_row["canonicalPerGameStageAuthority"][
        "unexpectedAuthority"
    ] = "tampered"
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert any(
        error.startswith(
            "canonical_overlay_existing_stage_authority_keyset_mismatch:"
        )
        and "unexpectedAuthority" in error
        for error in rejected["authorityErrors"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(
            {"scheduledLockAtUtc": "2026-08-27T00:00:00+00:00"}
        ),
        lambda row: row.update({"lockOutcomeRecorded": False}),
        lambda row: row["lastPossiblePredictionGate"].update(
            {"finalWindowActive": True}
        ),
        lambda row: row["slatePredictionLock"].update(
            {"policyVersion": "tampered-policy"}
        ),
        lambda row: row["tags"].append("PRE_LOCK_PREDICTION"),
    ],
)
def test_contradictory_public_lock_marker_never_uses_idempotent_path(
    mutation,
):
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    mutation(overlay)
    assert immutable_storage._canonical_read_overlay(overlay) is False
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = module._store_prediction(overlay)

    assert rejected["ok"] is False
    assert table.items == before
    assert write_calls == []


def test_incoming_extra_label_field_is_rejected_without_write():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    overlay["correct"] = True
    assert immutable_storage._canonical_read_overlay(overlay) is True
    table = module.history.PULLS
    before = copy.deepcopy(table.items)
    write_calls = _arm_zero_write_guard(table)

    rejected = _assert_rejected_without_write(
        module,
        overlay,
        before,
        write_calls,
    )

    assert (
        "canonical_overlay_immutable_projection_mismatch"
        in rejected["authorityErrors"]
    )

