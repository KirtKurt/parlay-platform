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

import mlb_immutable_locked_storage_patch as immutable_storage
import mlb_last_possible_prediction_gate as final_gate
import mlb_locked_prediction_storage_finalizer_v1 as finalizer


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


def locked_result(*, include_price=True):
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
        "lockedAtUtc": "2026-07-13T17:15:00+00:00",
        "predictionSourcePullAt": "2026-07-13T17:14:00+00:00",
        "tags": ["SLATE_LOCKED"],
    }
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
    immutable_storage.apply(module)
    finalizer.apply(module)
    return module, calls


def main() -> int:
    module, calls = module_for(locked_result())
    result = module.predict_all("2026-07-13", store=True, limit=500)

    assert calls and calls[0]["kwargs"]["store"] is False
    assert result["ok"] is True
    assert result["canonicalLockedStorageComplete"] is True
    assert result["canonicalLockedStoredCount"] == 1
    assert result["storedCount"] == 1
    assert len(module.history.PULLS.items) == 1

    row = result["predictions"][0]
    vector = row["frozenFeatureVector"]
    assert row["featureVectorFrozenAtLock"] is True
    assert row["frozenFeatureVectorVersion"] == vector["version"]
    assert vector["fingerprint"]
    assert vector["labels"] == {"homeWon": None, "pickCorrect": None}
    stored = next(iter(module.history.PULLS.items.values()))["data"]
    assert stored["frozenFeatureVector"] == vector
    assert stored["canonicalLockedStorageFinalizerVersion"] == finalizer.VERSION

    # Validation happens across the full card before the first immutable write.
    invalid_module, invalid_calls = module_for(locked_result(include_price=False))
    invalid = invalid_module.predict_all("2026-07-13", store=True, limit=500)
    assert invalid_calls[0]["kwargs"]["store"] is False
    assert invalid["ok"] is False
    assert invalid["storedCount"] == 0
    assert invalid_module.history.PULLS.items == {}
    errors = invalid["canonicalLockedStorageErrors"]["finalizer-game-1"]
    assert "selected_side_locked_price_not_proven" in errors

    # The last-possible gate no longer owns a direct DynamoDB write path.
    delegated = []
    delegate_module = SimpleNamespace(
        _store_prediction=lambda row: delegated.append(copy.deepcopy(row)) or {"ok": True, "pk": "p", "sk": "s"}
    )
    gate_result = final_gate._store_final({"gameId": "delegated-game"}, module=delegate_module)
    assert gate_result["ok"] is True and gate_result["finalGateStored"] is True
    assert delegated and delegated[0]["gameId"] == "delegated-game"

    print(
        "MLB locked storage finalizer verified: inner writes are suppressed, the final exact vector is stored once, "
        "invalid cards make zero writes, and the last-gate path delegates to the central guard."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
