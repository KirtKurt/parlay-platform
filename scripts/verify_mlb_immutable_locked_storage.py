#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

import mlb_immutable_locked_storage_patch as patch
import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
import mlb_ml_clean_cohort_v1 as cohort


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
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, *, Key, ConsistentRead=False):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": copy.deepcopy(item)} if item else {}


class FakeHistory:
    def __init__(self):
        self.PULLS = FakeTable()

    @staticmethod
    def ddb_safe(value):
        return value


def locked_row(base, *, game_id="provider-123", winner="Home Team"):
    row = {
        **base,
        "gameId": game_id,
        "gameIdentity": game_id,
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictedWinner": winner,
        "predictedSide": "home" if winner == "Home Team" else "away",
        "americanOdds": -120,
        "lockedAmericanOdds": -120,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "teamWinProbabilityPct": 55.0,
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1-test",
        "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "featureVectorFrozenAtLock": True,
        "tags": ["SLATE_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "slatePredictionLock": {
            "locked": True,
            "lockAtUtc": "2026-07-12T18:00:00+00:00",
            "latestScoringPullAt": "2026-07-12T17:55:00+00:00",
        },
        "slateCoverage": {"coverageComplete": True},
        "mlFeatureFreeze": {
            "exactVectorCreated": True,
            "completeSlateCoverage": True,
            "trainingEligible": True,
        },
    }
    vector = {
        "version": vector_contract.EXPECTED_VECTOR_VERSION,
        "createdAtUtc": "2026-07-12T18:00:00+00:00",
        "sourcePullAtUtc": "2026-07-12T17:55:00+00:00",
        "lockAtUtc": "2026-07-12T18:00:00+00:00",
        "gameId": game_id,
        "slateDateEt": base["slate_date"],
        "commenceTime": base["commenceTime"],
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictedWinner": winner,
        "predictedSide": row["predictedSide"],
        "selectedAmericanOdds": -120,
        "selectedPriceBook": "fanduel",
        "selectedPriceSource": "real_book",
        "features": {"homeMarketProb": 0.55, "awayMarketProb": 0.45},
        "labels": {"homeWon": None, "pickCorrect": None},
        "immutableSource": "locked_prediction_row_pre_game_features",
        "derivedOnceFromImmutableLockedRow": True,
        "fingerprintVersion": cohort.FINGERPRINT_VERSION,
    }
    vector["fingerprint"] = cohort.fingerprint_for_vector(vector)
    row["frozenFeatureVector"] = vector
    row["frozenFeatureVectorVersion"] = vector["version"]
    return row


def main() -> int:
    history = FakeHistory()
    module = SimpleNamespace(history=history)

    def original_store(row):
        item = {
            "PK": f"GAME_WINNERS#mlb#{row['slate_date']}",
            "SK": f"GAME#{row['commenceTime']}#{row['gameIdentity']}",
            "data": copy.deepcopy(row),
        }
        history.PULLS.put_item(Item=item)
        return {"ok": True, "pk": item["PK"], "sk": item["SK"]}

    module._store_prediction = original_store
    patch.apply(module)

    base = {
        "slate_date": "2026-07-12",
        "gameId": "provider-123",
        "gameIdentity": "provider-123",
        "commenceTime": "2026-07-12T20:00:00Z",
        "predictedWinner": "Home Team",
        "createdAt": "2026-07-12T18:00:00Z",
    }

    live = dict(base)
    live_result = module._store_prediction(live)
    assert live_result["storageClass"] == "LIVE_MUTABLE"

    # Locked writes fail closed until the exact fingerprinted ML vector exists.
    missing_vector = {
        **base,
        "lockedPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "slatePredictionLock": {"locked": True},
    }
    before_missing = copy.deepcopy(history.PULLS.items)
    try:
        module._store_prediction(missing_vector)
    except RuntimeError as exc:
        assert "missing_frozen_feature_vector" in str(exc)
    else:
        raise AssertionError("locked storage accepted a vectorless row")
    assert history.PULLS.items == before_missing

    locked = locked_row(base)
    locked_result = module._store_prediction(locked)
    assert locked_result["storageClass"] == "LOCKED_IMMUTABLE"
    assert locked_result["created"] is True

    live_later = {**base, "predictedWinner": "Away Team", "createdAt": "2026-07-12T22:00:00Z"}
    module._store_prediction(live_later)

    locked_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "LOCKED#GAME#2026-07-12T20:00:00Z#provider-123",
    )
    live_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "GAME#2026-07-12T20:00:00Z#provider-123",
    )
    assert history.PULLS.items[locked_key]["data"]["predictedWinner"] == "Home Team"
    assert history.PULLS.items[live_key]["data"]["predictedWinner"] == "Away Team"

    repeated = module._store_prediction(copy.deepcopy(locked))
    assert repeated["immutableExisting"] is True
    assert repeated["exactVectorVerified"] is True

    changed_lock = locked_row(base, winner="Away Team")
    try:
        module._store_prediction(changed_lock)
    except RuntimeError as exc:
        assert "COLLISION_MISMATCH" in str(exc)
    else:
        raise AssertionError("immutable collision accepted a changed locked vector")
    assert history.PULLS.items[locked_key]["data"]["predictedWinner"] == "Home Team"

    tampered = locked_row({**base, "commenceTime": "2026-07-12T21:00:00Z"}, game_id="provider-tampered")
    tampered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    before_tampered = copy.deepcopy(history.PULLS.items)
    try:
        module._store_prediction(tampered)
    except RuntimeError as exc:
        assert "frozen_vector_fingerprint_mismatch" in str(exc)
    else:
        raise AssertionError("locked storage accepted a tampered fingerprint")
    assert history.PULLS.items == before_tampered

    # A legacy/vectorless row already occupying the write-once key is never
    # treated as success or repaired after outcomes are known.
    legacy = locked_row({**base, "commenceTime": "2026-07-12T22:00:00Z"}, game_id="provider-legacy")
    legacy_key = (
        "GAME_WINNERS#mlb#2026-07-12",
        "LOCKED#GAME#2026-07-12T22:00:00Z#provider-legacy",
    )
    history.PULLS.items[legacy_key] = {
        "PK": legacy_key[0],
        "SK": legacy_key[1],
        "data": {"gameId": "provider-legacy", "lockedPrediction": True},
    }
    try:
        module._store_prediction(legacy)
    except RuntimeError as exc:
        assert "existing_collision" in str(exc)
    else:
        raise AssertionError("vectorless existing collision was accepted")

    assert len(history.PULLS.items) == 3
    print(
        "MLB immutable locked storage verified: live rows remain mutable; locked rows require an exact vector; "
        "tampered, changed, or vectorless collisions fail closed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
