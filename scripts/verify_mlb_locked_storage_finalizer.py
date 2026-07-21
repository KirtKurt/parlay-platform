#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_daily_per_game_lock_patch as per_game
import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
import mlb_last_possible_prediction_gate as final_gate
import mlb_locked_prediction_storage_finalizer_v1 as finalizer
import mlb_ml_clean_cohort_v1 as cohort
import mlb_slate_coverage_patch as coverage
from mlb_ml_feature_test_fixtures import attach_lock_safe_features
from scripts.verify_mlb_immutable_locked_storage import seed_stage as seed_full_authority_stage


class FakeTable:
    def __init__(self):
        self.items = {}

    def put_item(self, *, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        self.items[key] = copy.deepcopy(Item)

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}


class FakeHistory:
    def __init__(self):
        self.PULLS = FakeTable()

    @staticmethod
    def ddb_safe(value):
        return copy.deepcopy(value)


def locked_result(*, include_price=True, authorized=False):
    row = {
        "slate_date": "2026-07-13",
        "gameId": "finalizer-game-1",
        "gameIdentity": "finalizer-game-1",
        "gameKey": "mlb|2026-07-13|away club|home club",
        "commenceTime": "2026-07-13T18:00:00+00:00",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "americanOdds": -120 if include_price else None,
        "lockedAmericanOdds": -120 if include_price else None,
        "priceBook": "fanduel" if include_price else None,
        "priceSource": "real_book" if include_price else "missing",
        "teamWinProbabilityPct": 55.0,
        "winProbabilityPct": 55.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "score": 60.0,
        "homeSignal": {
            "marketConsensusProbability": 0.55,
            "probLatest": 0.55,
            "americanOdds": -120 if include_price else None,
            "priceBook": "fanduel" if include_price else None,
            "priceSource": "real_book" if include_price else "missing",
            "tags": ["BOOK_AGREEMENT"],
        },
        "awaySignal": {
            "marketConsensusProbability": 0.45,
            "probLatest": 0.45,
            "americanOdds": 110 if include_price else None,
            "priceBook": "fanduel" if include_price else None,
            "priceSource": "real_book" if include_price else "missing",
            "tags": ["BOOK_AGREEMENT"],
        },
        "fundamentalsSnapshot": {"completenessRatio": 0.0, "numericValues": {}},
        "slatePredictionLock": {
            "locked": True,
            "slateWideLock": True,
            "lockAtUtc": "2026-07-13T17:15:00+00:00",
            "latestScoringPullAt": "2026-07-13T17:14:00+00:00",
        },
        "lockedPrediction": True,
        "immutablePerGameStage": authorized,
        "lockedAtUtc": "2026-07-13T17:15:00+00:00",
        "predictionSourcePullAt": "2026-07-13T17:14:00+00:00",
        "featureVectorFrozenAtLock": True,
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "mlFeatureFreeze": {
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
        "tags": ["SLATE_LOCKED"],
    }
    if authorized:
        row.update({
            "lastPrelockSelectionFingerprint": "selection-finalizer-game-1",
            "lastPrelockPromotionVersion": per_game.PROMOTION_POLICY_VERSION,
            "modelOrSignalRecomputedAtLock": False,
        })
    attach_lock_safe_features(row)
    vector = cohort.freeze_feature_snapshot(row)
    row["frozenFeatureVector"] = vector
    row["frozenFeatureVectorVersion"] = vector["version"]
    if authorized:
        row["lastPrelockSelectionFingerprint"] = per_game._payload_fingerprint(
            per_game._selection_material(row)
        )
        row = vector_contract.apply_exact_vector_training_status(row)
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date": "2026-07-13",
        "gameCount": 1,
        "count": 1,
        "allGamesPredicted": True,
        "slatePredictionLock": copy.deepcopy(row["slatePredictionLock"]),
        "slateCoverage": {
            "coverageComplete": True,
            "manifestGameCount": 1,
            "predictionGameCount": 1,
        },
        "predictions": [row],
    }


def module_for(result):
    calls = []
    history = FakeHistory()

    def predict_all(*args, **kwargs):
        calls.append({"args": args, "kwargs": copy.deepcopy(kwargs)})
        return copy.deepcopy(result)

    def live_store(row):
        item = {
            "PK": f"GAME_WINNERS#mlb#{row.get('slate_date')}",
            "SK": f"GAME#{row.get('commenceTime')}#{row.get('gameIdentity')}",
            "data": copy.deepcopy(row),
        }
        history.PULLS.put_item(Item=item)
        return {"ok": True, "pk": item["PK"], "sk": item["SK"]}

    module = SimpleNamespace(
        predict_all=predict_all,
        _store_prediction=live_store,
        history=history,
    )
    for row in result.get("predictions") or []:
        if row.get("immutablePerGameStage") is not True:
            continue
        seed_full_authority_stage(history, row)
    immutable_storage.apply(module)
    finalizer.apply(module)
    return module, calls


def main() -> int:
    # A generic/legacy locked result is still returned to the caller, but it
    # cannot manufacture a canonical row merely because store=True was passed.
    module, calls = module_for(locked_result())
    result = module.predict_all("2026-07-13", store=True, limit=500)

    assert calls and calls[0]["kwargs"]["store"] is False
    assert result["ok"] is True
    assert result["canonicalLockedStorageComplete"] is False
    assert result["canonicalLockedStoredCount"] == 0
    assert result["canonicalLockedStorageCandidateCount"] == 0
    assert result["canonicalLockedStorageSuppressedUnauthorizedCount"] == 1
    assert result["storedCount"] == 0
    assert module.history.PULLS.items == {}
    generic_row = result["predictions"][0]
    assert generic_row["immutablePerGameStage"] is False
    assert generic_row["canonicalLockedStoreSuppressed"] is True
    assert generic_row["canonicalLockedStoreSuppressionReason"] == finalizer.UNAUTHORIZED_LOCKED_WRITE

    # An exact row that already came from the immutable per-game stage is the
    # only row the finalizer may pass into the canonical store.
    stage_module, stage_calls = module_for(locked_result(authorized=True))
    stage_result = stage_module.predict_all("2026-07-13", store=True, limit=500)
    assert stage_calls and stage_calls[0]["kwargs"]["store"] is False
    assert stage_result["ok"] is True
    assert stage_result["canonicalLockedStorageComplete"] is True
    assert stage_result["canonicalLockedStoredCount"] == 1
    assert stage_result["canonicalLockedStorageCandidateCount"] == 1
    assert stage_result["canonicalLockedStorageSuppressedUnauthorizedCount"] == 0
    assert stage_result["storedCount"] == 1
    assert sum(
        sk.startswith("LOCKED#GAME#")
        for _, sk in stage_module.history.PULLS.items
    ) == 1

    row = stage_result["predictions"][0]
    vector = row["frozenFeatureVector"]
    assert row["featureVectorFrozenAtLock"] is True
    assert row["frozenFeatureVectorVersion"] == vector["version"]
    assert vector["fingerprint"]
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    stored = next(
        item["data"]
        for (pk, sk), item in stage_module.history.PULLS.items.items()
        if sk.startswith("LOCKED#GAME#")
    )
    assert stored["frozenFeatureVector"] == vector
    assert stored["immutablePerGameStage"] is True
    repeated_stage = stage_module.predict_all("2026-07-13", store=True, limit=500)
    assert repeated_stage["canonicalLockedStorageComplete"] is True
    assert repeated_stage["canonicalLockedStoredCount"] == 1
    assert repeated_stage["predictions"][0]["canonicalLockedStore"]["immutableExisting"] is True
    assert sum(
        sk.startswith("LOCKED#GAME#")
        for _, sk in stage_module.history.PULLS.items
    ) == 1

    # Invalid authorized stages fail closed without a canonical write.
    invalid_module, invalid_calls = module_for(locked_result(include_price=False, authorized=True))
    invalid = invalid_module.predict_all("2026-07-13", store=True, limit=500)
    assert invalid_calls[0]["kwargs"]["store"] is False
    assert invalid["ok"] is False
    assert invalid["storedCount"] == 0
    assert not any(
        sk.startswith("LOCKED#GAME#")
        for _, sk in invalid_module.history.PULLS.items
    )
    errors = invalid["canonicalLockedStorageErrors"]["finalizer-game-1"]
    assert any(
        "selected_side_locked_price_not_proven" in str(error)
        for error in errors
    )

    # Mixed outputs are handled row by row.  A top-level locked flag must not
    # convert the pre-lock row, and the unauthorized locked row is suppressed
    # without blocking either valid storage class.
    pre_lock = copy.deepcopy(locked_result()["predictions"][0])
    pre_lock.update({
        "gameId": "pre-lock-game",
        "gameIdentity": "pre-lock-game",
        "gameKey": "mlb|2026-07-13|pre away|pre home",
        "commenceTime": "2026-07-13T19:00:00+00:00",
        "immutablePerGameStage": False,
    })
    for key in (
        "lockedPrediction", "officialPrediction", "officialPredictionStatus",
        "slatePredictionLock", "lastPossiblePredictionGate", "lockedCardAudit",
    ):
        pre_lock.pop(key, None)
    pre_lock["tags"] = []
    unauthorized = copy.deepcopy(locked_result()["predictions"][0])
    unauthorized.update({
        "gameId": "legacy-locked-game",
        "gameIdentity": "legacy-locked-game",
        "commenceTime": "2026-07-13T20:00:00+00:00",
    })
    authorized = copy.deepcopy(locked_result(authorized=True)["predictions"][0])
    mixed_result = locked_result()
    mixed_result["predictions"] = [pre_lock, unauthorized, authorized]
    mixed_result["count"] = 3
    mixed_result["gameCount"] = 3
    mixed_module, mixed_calls = module_for(mixed_result)
    mixed = mixed_module.predict_all("2026-07-13", store=True, limit=500)
    assert mixed_calls[0]["kwargs"]["store"] is False
    assert mixed["preLockStoredCount"] == 1
    assert mixed["canonicalLockedStoredCount"] == 1
    assert mixed["canonicalLockedStorageSuppressedUnauthorizedCount"] == 1
    assert mixed["storedCount"] == 2
    assert mixed["predictions"][0].get("lockedPrediction") is not True
    assert mixed["predictions"][0].get("officialPredictionStatus") != "OFFICIAL_LOCKED_PREDICTION"
    keys = set(mixed_module.history.PULLS.items)
    assert any(sk.startswith("GAME#") for _, sk in keys)
    assert sum(sk.startswith("LOCKED#GAME#") for _, sk in keys) == 1

    # The last-possible gate no longer owns a direct DynamoDB write path.
    delegated = []
    delegate_module = SimpleNamespace(
        _store_prediction=lambda row: delegated.append(copy.deepcopy(row)) or {"ok": True, "pk": "p", "sk": "s"}
    )
    gate_result = final_gate._store_final({"gameId": "delegated-game"}, module=delegate_module)
    assert gate_result["ok"] is True and gate_result["finalGateStored"] is True
    assert delegated and delegated[0]["gameId"] == "delegated-game"

    print(
        "MLB locked storage finalizer verified: inner writes are suppressed; generic locked output cannot create "
        "canonical rows; mixed output is handled row by row; exact immutable per-game stages store idempotently; "
        "invalid stages make zero canonical writes; and the last-gate path delegates to the central guard."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
