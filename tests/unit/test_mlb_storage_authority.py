from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
import pytest


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_locked_prediction_storage_finalizer_v1 as finalizer
import mlb_last_possible_prediction_gate as legacy_gate


def _user_visible_prelock(engine, row):
    out = copy.deepcopy(row)
    out.update({
        "lockedPrediction": False,
        "officialPrediction": False,
        "officialPick": False,
        "officialPredictionStatus": engine.PREGAME_DISPLAY_STATUS,
        "displayPrediction": True,
        "displayGroup": "pre_lock_prediction",
        "perGameCanonicalLock": {
            "authorityVersion": engine.PUBLIC_PRELOCK_AUTHORITY_VERSION,
            "status": "OPEN_PRE_LOCK",
            "canonical": False,
        },
        "signalPolicyV13": {
            "applied": True,
            "version": "MLB-SIGNAL-POLICY-v-test",
        },
    })
    out["tags"] = sorted(set((out.get("tags") or []) + ["PRE_LOCK_PREDICTION"]))
    return out


def test_pregame_snapshot_payload_fingerprint_survives_production_ddb_round_trip():
    import mlb_daily_per_game_lock_patch as per_game_lock
    import mlb_game_winner_engine as engine
    import inqsi_pull_history as history_contract

    row = _user_visible_prelock(engine, {
        "slate_date": "2026-07-17",
        "gameId": "game-ddb-fingerprint",
        "gameIdentity": "game-ddb-fingerprint",
        "commenceTime": "2026-07-17T18:00:00+00:00",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "americanOdds": -118.0,
        "lockedAmericanOdds": -118.0,
        "score": 100.0,
        "winProbability": 0.3753,
        "expectedValue": 0.015137,
        "championReliabilityThreshold": None,
        "homeSignal": {
            "score": 34.65,
            "americanOdds": -118.0,
            "zero": 0.0,
            "one": 1.0,
            "nested": [100.0, 0.3753, None],
        },
        "frozenFeatureVector": {
            "version": "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v-test",
            "labels": {"homeWon": None, "pickCorrect": None},
        },
        "predictionSourcePullAt": "2026-07-17T17:15:00+00:00",
        "predictionSourcePullId": "pull-ddb-fingerprint",
        "createdAt": "2026-07-17T17:15:30+00:00",
    })

    snapshot = engine._pregame_snapshot_item(
        row,
        persisted_at="2026-07-17T17:15:31+00:00",
    )
    readback = TypeDeserializer().deserialize(TypeSerializer().serialize(snapshot))
    persisted_row = readback["data"]

    assert "championReliabilityThreshold" not in persisted_row
    assert persisted_row["frozenFeatureVector"]["labels"] == {
        "homeWon": None,
        "pickCorrect": None,
    }
    assert persisted_row["americanOdds"] == Decimal("-118.0")
    assert persisted_row["homeSignal"]["score"] == Decimal("34.65")
    assert persisted_row["homeSignal"]["nested"][-1] is None
    assert snapshot["prediction_payload_fingerprint_version"] == (
        history_contract.CANONICAL_PAYLOAD_FINGERPRINT_VERSION
    )
    assert engine.PAYLOAD_FINGERPRINT_VERSION == per_game_lock.PAYLOAD_FINGERPRINT_VERSION
    assert engine.PREGAME_SNAPSHOT_VERSION == per_game_lock.PREGAME_SNAPSHOT_VERSION
    assert engine.PREGAME_SNAPSHOT_ROLE == per_game_lock.PREGAME_SNAPSHOT_ROLE
    assert engine.PUBLIC_PRELOCK_AUTHORITY_VERSION == (
        per_game_lock.PREGAME_PUBLIC_AUTHORITY_VERSION
    )
    assert snapshot["prediction_payload_fingerprint"] == per_game_lock._payload_fingerprint(
        persisted_row
    )
    assert snapshot["snapshot_version"] == engine.PREGAME_SNAPSHOT_VERSION
    assert snapshot["snapshot_role"] == engine.PREGAME_SNAPSHOT_ROLE
    assert snapshot["public_authority_version"] == engine.PUBLIC_PRELOCK_AUTHORITY_VERSION
    assert snapshot["user_visible"] is True
    assert snapshot["display_prediction"] is True
    assert snapshot["display_status"] == engine.PREGAME_DISPLAY_STATUS
    assert snapshot["display_surface"] == engine.PREGAME_DISPLAY_SURFACE
    assert snapshot["signal_policy_version"] == "MLB-SIGNAL-POLICY-v-test"

    tampered = copy.deepcopy(persisted_row)
    tampered["americanOdds"] = Decimal("-117")
    assert snapshot["prediction_payload_fingerprint"] != per_game_lock._payload_fingerprint(
        tampered
    )


def test_exact_payload_fingerprint_distinguishes_low_order_decimal_tampering():
    import inqsi_pull_history as history_contract

    original = Decimal("0.12345678901234567890123456789012345678")
    tampered = Decimal("0.12345678901234567890123456789012345679")

    assert float(original) == float(tampered)
    assert history_contract.canonical_payload_fingerprint({"value": original}) != (
        history_contract.canonical_payload_fingerprint({"value": tampered})
    )
    assert history_contract.canonical_payload_fingerprint({"value": 100}) == (
        history_contract.canonical_payload_fingerprint({"value": Decimal("100.0")})
    )


def test_legacy_payload_fingerprint_handles_full_precision_integral_decimal():
    import inqsi_pull_history as history_contract

    value = Decimal("12345678901234567890123456789012345678")

    assert history_contract.legacy_payload_fingerprint({"value": value}) == (
        history_contract.legacy_payload_fingerprint({"value": int(value)})
    )


def test_pregame_persistence_time_is_sampled_after_successful_live_put(monkeypatch):
    import mlb_game_winner_engine as engine

    events = []
    written = []

    class Table:
        def put_item(self, Item, ConditionExpression=None):
            events.append(("put", Item.get("record_type")))
            written.append(copy.deepcopy(Item))
            return {}

        def get_item(self, **kwargs):
            return {}

    def acknowledged_at():
        events.append(("clock", "after-live-write"))
        return "2026-07-16T22:14:59+00:00"

    monkeypatch.setattr(engine.history, "PULLS", Table())
    monkeypatch.setattr(engine.history, "ddb_safe", copy.deepcopy)
    monkeypatch.setattr(engine, "_now", acknowledged_at)

    result = engine._store_prediction(_user_visible_prelock(engine, {
        "slate_date": "2026-07-16",
        "gameId": "game-1",
        "gameIdentity": "game-1",
        "commenceTime": "2026-07-16T23:00:00+00:00",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "createdAt": "2026-07-16T22:14:58+00:00",
    }))

    assert result["ok"] is True
    assert events == [
        ("put", "mlb_single_game_moneyline_prediction"),
        ("clock", "after-live-write"),
        ("put", engine.PREGAME_SNAPSHOT_RECORD_TYPE),
    ]
    snapshot = written[-1]
    assert snapshot["prediction_persisted_at_utc"] == "2026-07-16T22:14:59+00:00"
    assert snapshot["prediction_persistence_proof_type"] == engine.PREGAME_PERSISTENCE_PROOF_TYPE


def test_raw_engine_row_cannot_create_authoritative_pregame_snapshot(monkeypatch):
    import mlb_game_winner_engine as engine

    writes = []

    class Table:
        def put_item(self, **kwargs):
            writes.append(copy.deepcopy(kwargs))

    monkeypatch.setattr(engine.history, "PULLS", Table())

    raw = {
        "slate_date": "2026-07-17",
        "gameId": "raw-engine-row",
        "gameIdentity": "raw-engine-row",
        "commenceTime": "2026-07-17T18:00:00+00:00",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "createdAt": "2026-07-17T17:00:00+00:00",
    }

    result = engine._store_prediction(raw)

    assert result["ok"] is False
    assert result["stored"] is False
    assert result["suppressed"] is True
    assert result["error"] == (
        "MLB_PREGAME_SNAPSHOT_REQUIRES_USER_VISIBLE_PLATFORM_PRELOCK"
    )
    assert result["storageClass"] == "PREGAME_REJECTED"
    assert "display_status_mismatch" in result["authorityErrors"]
    assert writes == []


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (lambda row: row.update(displayPrediction=False), "display_prediction_not_true"),
        (lambda row: row.pop("signalPolicyV13"), "signal_policy_version_missing"),
        (
            lambda row: row.update(signalPolicyV13={"applied": True, "version": ""}),
            "signal_policy_version_missing",
        ),
    ],
)
def test_public_snapshot_marker_requires_real_display_and_signal_policy(
    monkeypatch,
    mutation,
    expected_error,
):
    import mlb_game_winner_engine as engine

    writes = []

    class Table:
        def put_item(self, **kwargs):
            writes.append(copy.deepcopy(kwargs))

    monkeypatch.setattr(engine.history, "PULLS", Table())
    row = _user_visible_prelock(engine, {
        "slate_date": "2026-07-17",
        "gameId": "marker-test",
        "gameIdentity": "marker-test",
        "commenceTime": "2026-07-17T18:00:00+00:00",
        "predictedWinner": "Home",
        "predictedSide": "home",
        "createdAt": "2026-07-17T17:00:00+00:00",
    })
    mutation(row)

    result = engine._store_prediction(row)

    assert result["ok"] is False
    assert result["storageClass"] == "PREGAME_REJECTED"
    assert expected_error in result["authorityErrors"]
    assert writes == []


def test_direct_legacy_locked_write_is_suppressed_before_any_store():
    original_calls = []
    table_calls = []

    def original_store(row):
        original_calls.append(copy.deepcopy(row))
        return {"ok": True}

    class Table:
        def put_item(self, **kwargs):
            table_calls.append(copy.deepcopy(kwargs))

    module = SimpleNamespace(
        _store_prediction=original_store,
        history=SimpleNamespace(PULLS=Table()),
    )
    immutable_storage.apply(module)

    result = module._store_prediction({
        "gameId": "legacy-locked",
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "slatePredictionLock": {"locked": True},
    })

    assert result["ok"] is False
    assert result["suppressed"] is True
    assert result["error"] == immutable_storage.UNAUTHORIZED_LOCKED_WRITE
    assert result["requiredAuthority"] == "verified immutable T-minus-45 stage record"
    assert original_calls == []
    assert table_calls == []

    pre_lock_result = module._store_prediction({
        "gameId": "visible-pre-lock",
        "officialPrediction": True,
        "officialPredictionStatus": "PRE_LOCK_PLATFORM_PREDICTION",
    })
    assert pre_lock_result["ok"] is True
    assert pre_lock_result["storageClass"] == "LIVE_MUTABLE"
    assert [row["gameId"] for row in original_calls] == ["visible-pre-lock"]


def test_finalizer_handles_mixed_rows_without_promoting_legacy_rows(monkeypatch):
    validator = ModuleType("mlb_daily_lock_ml_vector_preservation_patch")
    validator.validate_exact_locked_row = lambda row: []
    monkeypatch.setitem(sys.modules, validator.__name__, validator)

    pre_lock = {
        "gameId": "pre-lock",
        "predictedWinner": "Away",
        # Some legacy display overlays use this flag for any visible winner;
        # it is not lock authority by itself.
        "officialPrediction": True,
        "officialPredictionStatus": "PRE_LOCK_PLATFORM_PREDICTION",
    }
    legacy_locked = {
        "gameId": "legacy-locked",
        "predictedWinner": "Home",
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
    }
    authorized_stage = {
        "gameId": "authorized-stage",
        "predictedWinner": "Home",
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "immutablePerGameStage": True,
    }
    source_result = {
        "ok": True,
        # A result-level lock must not promote the pre-lock row.
        "slatePredictionLock": {"locked": True},
        "predictions": [pre_lock, legacy_locked, authorized_stage],
    }
    inner_calls = []
    store_calls = []

    def predict_all(*args, **kwargs):
        inner_calls.append({"args": args, "kwargs": copy.deepcopy(kwargs)})
        return copy.deepcopy(source_result)

    def store_prediction(row):
        store_calls.append(copy.deepcopy(row))
        return {"ok": True, "gameId": row["gameId"]}

    module = SimpleNamespace(
        predict_all=predict_all,
        _store_prediction=store_prediction,
    )
    finalizer.apply(module)

    result = module.predict_all("2026-07-16", store=True, limit=500)

    assert inner_calls[0]["kwargs"]["store"] is False
    assert [row["gameId"] for row in store_calls] == ["pre-lock", "authorized-stage"]
    assert result["preLockStoredCount"] == 1
    assert result["canonicalLockedStorageCandidateCount"] == 1
    assert result["canonicalLockedStoredCount"] == 1
    assert result["canonicalLockedStorageSuppressedUnauthorizedCount"] == 1
    assert result["canonicalLockedStorageComplete"] is True
    rows = {row["gameId"]: row for row in result["predictions"]}
    assert rows["pre-lock"].get("lockedPrediction") is not True
    assert rows["pre-lock"].get("immutablePerGameStage") is not True
    assert rows["pre-lock"]["preLockStore"]["ok"] is True
    assert rows["legacy-locked"]["canonicalLockedStoreSuppressed"] is True
    assert rows["legacy-locked"]["canonicalLockedStoreSuppressionReason"] == finalizer.UNAUTHORIZED_LOCKED_WRITE
    assert "canonicalLockedStore" not in rows["legacy-locked"]
    assert rows["authorized-stage"]["canonicalLockedStore"]["ok"] is True


def test_invalid_authorized_stage_does_not_block_pre_lock_storage(monkeypatch):
    validator = ModuleType("mlb_daily_lock_ml_vector_preservation_patch")
    validator.validate_exact_locked_row = lambda row: ["bad_exact_vector"]
    monkeypatch.setitem(sys.modules, validator.__name__, validator)

    result_template = {
        "ok": True,
        "predictions": [
            {"gameId": "pre-lock", "predictedWinner": "Away"},
            {
                "gameId": "invalid-stage",
                "predictedWinner": "Home",
                "lockedPrediction": True,
                "immutablePerGameStage": True,
            },
        ],
    }
    stored_ids = []
    module = SimpleNamespace(
        predict_all=lambda *args, **kwargs: copy.deepcopy(result_template),
        _store_prediction=lambda row: stored_ids.append(row["gameId"]) or {"ok": True},
    )
    finalizer.apply(module)

    result = module.predict_all("2026-07-16", store=True)

    assert stored_ids == ["pre-lock"]
    assert result["preLockStoredCount"] == 1
    assert result["canonicalLockedStoredCount"] == 0
    assert result["canonicalLockedStorageErrors"] == {"invalid-stage": ["bad_exact_vector"]}
    assert result["ok"] is False
    assert result["operationalDefect"] is True


def test_pre_lock_storage_failure_marks_candidate_run_failed():
    source_result = {
        "ok": True,
        "allGamesPredicted": True,
        "predictions": [
            {"gameId": "stored", "predictedWinner": "Home"},
            {"gameId": "failed", "predictedWinner": "Away"},
        ],
    }

    def store_prediction(row):
        if row["gameId"] == "failed":
            return {"ok": False, "error": "injected persistence failure"}
        return {"ok": True}

    module = SimpleNamespace(
        predict_all=lambda *args, **kwargs: copy.deepcopy(source_result),
        _store_prediction=store_prediction,
    )
    finalizer.apply(module)

    result = module.predict_all("2026-07-16", store=True)

    assert result["preLockStorageCandidateCount"] == 2
    assert result["preLockStoredCount"] == 1
    assert result["preLockStorageComplete"] is False
    assert result["preLockStorageErrors"]
    assert result["ok"] is False
    assert result["operationalDefect"] is True
    assert result["allGamesPredicted"] is False


def test_legacy_twelve_hour_gate_is_bypassed_for_candidate_persistence(monkeypatch):
    monkeypatch.setattr(
        legacy_gate,
        "_now_utc",
        lambda: datetime(2026, 7, 16, 17, 15, tzinfo=timezone.utc),
    )
    source_result = {
        "ok": True,
        "sport": "mlb",
        "slate_date": "2026-07-16",
        "gameCount": 1,
        "allGamesPredicted": True,
        "slatePredictionLock": {
            "locked": False,
            "slateWideLock": False,
            "perGameLock": True,
        },
        "predictions": [
            {
                "gameId": "tminus45-candidate",
                "gameIdentity": "tminus45-candidate",
                "commenceTime": "2026-07-16T18:00:00+00:00",
                "predictedWinner": "Home Club",
                "predictedSide": "home",
                "tags": [],
            }
        ],
    }
    stored = []
    module = SimpleNamespace(
        predict_all=lambda *args, **kwargs: copy.deepcopy(source_result),
        _store_prediction=lambda row: stored.append(copy.deepcopy(row)) or {"ok": True},
        history=SimpleNamespace(PULLS=SimpleNamespace()),
    )
    legacy_gate.apply(module)
    immutable_storage.apply(module)
    finalizer.apply(module)
    module._INQSI_MLB_CANONICAL_PER_GAME_AUTHORITY_ENABLED = True

    result = module.predict_all("2026-07-16", store=True)

    assert result["preLockStorageComplete"] is True
    assert result["preLockStoredCount"] == 1
    assert len(stored) == 1
    assert stored[0].get("lockedPrediction") is not True
    assert "FINAL_LOCKED" not in (stored[0].get("tags") or [])
