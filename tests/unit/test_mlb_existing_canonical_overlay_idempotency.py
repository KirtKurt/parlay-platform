from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_locked_prediction_storage_finalizer_v1 as finalizer
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
    assert row["officialPredictionReason"] == (
        "validated_immutable_canonical_per_game_lock"
    )
    assert row["slateCoverageVersion"] == coverage.VERSION
    assert row["immutablePerGameStage"] is True
    assert row["immutableLockedStorage"] is True
    assert row["immutableLockedStorageKeyspace"] == "LOCKED#GAME"
    assert row["immutableLockedStorageVersion"] == immutable_storage.VERSION
    assert row["canonical"] is True
    assert row["lockedPrediction"] is True
    assert row["lockStatus"] == "LOCKED_CANONICAL"
    assert row["officialPredictionStatus"] == (
        "OFFICIAL_LOCKED_PREDICTION"
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
    assert public_lock["authorityVersion"] == coverage.AUTHORITY_VERSION
    assert public_lock["canonicalReadOperational"] is True
    assert public_lock["perGameLock"] is True
    assert public_lock["slateWideLock"] is False
    assert immutable_storage._canonical_read_overlay(row) is True


def _module_with_existing_canonical_rows(count):
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
        seed_stage(history, row)
        created = module._store_prediction(row)
        assert created["ok"] is True
        assert created["created"] is True
        rows.append(row)
    mutable_store_calls.clear()
    return module, rows, mutable_store_calls


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
            "officialPredictionStatus": "PRE_LOCK_PLATFORM_PREDICTION",
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

    before = copy.deepcopy(module.history.PULLS.items)
    result = module.predict_all(SLATE, store=True)
    after = copy.deepcopy(module.history.PULLS.items)

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

    canonical_results = [
        row["canonicalLockedStore"]
        for row in result["predictions"][:2]
    ]
    assert all(
        stored["immutableExisting"] is True
        and stored["idempotentExistingVerified"] is True
        and stored["canonicalReadOverlayVerified"] is True
        and stored["canonicalWriteAttempted"] is False
        for stored in canonical_results
    )


def _assert_rejected_without_write(module, row, before):
    rejected = module._store_prediction(row)
    assert rejected["ok"] is False
    assert rejected["canonicalWriteAttempted"] is False
    assert rejected["canonicalReadOverlayVerified"] is False
    assert module.history.PULLS.items == before
    return rejected


def test_existing_overlay_immutable_tampering_still_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    _assert_live_overlay_markers(overlay)
    before = copy.deepcopy(module.history.PULLS.items)

    wrong_side = copy.deepcopy(overlay)
    wrong_side["predictedWinner"] = "Away Team"
    wrong_side["predictedSide"] = "away"
    rejected_side = _assert_rejected_without_write(
        module,
        wrong_side,
        before,
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
    )
    assert any(
        "vector" in error
        for error in rejected_vector["authorityErrors"]
    )


def test_existing_overlay_missing_item_or_read_failure_still_fails_closed():
    module, canonical_rows, _ = _module_with_existing_canonical_rows(1)
    overlay = _canonical_public_overlay(canonical_rows[0])
    before = copy.deepcopy(module.history.PULLS.items)
    table = module.history.PULLS
    original_get_item = table.get_item

    def fail_read(*args, **kwargs):
        raise RuntimeError("injected consistent-read failure")

    table.get_item = fail_read
    read_failure = _assert_rejected_without_write(
        module,
        overlay,
        before,
    )
    assert any(
        error.startswith(
            "canonical_overlay_consistent_read_failed:RuntimeError:"
        )
        for error in read_failure["authorityErrors"]
    )

    table.get_item = original_get_item
    key = immutable_storage._locked_key(overlay)
    table.items.pop((key["PK"], key["SK"]))
    missing = module._store_prediction(overlay)

    assert missing["ok"] is False
    assert missing["canonicalWriteAttempted"] is False
    assert missing["canonicalReadOverlayVerified"] is False
    assert missing["authorityErrors"] == [
        "canonical_overlay_existing_locked_item_missing"
    ]
