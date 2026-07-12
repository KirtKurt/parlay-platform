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

    locked = {
        **base,
        "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "tags": ["SLATE_LOCKED", "OFFICIAL_LOCKED_PREDICTION"],
        "slatePredictionLock": {"locked": True, "lockAtUtc": "2026-07-12T18:00:00Z"},
    }
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

    changed_lock = {**locked, "predictedWinner": "Away Team", "createdAt": "2026-07-12T23:00:00Z"}
    repeated = module._store_prediction(changed_lock)
    assert repeated["immutableExisting"] is True
    assert history.PULLS.items[locked_key]["data"]["predictedWinner"] == "Home Team"

    assert len(history.PULLS.items) == 2
    print("MLB immutable locked storage verified: live rows cannot overwrite locked rows and locked rows are write-once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
