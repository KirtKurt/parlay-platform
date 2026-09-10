from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from ratings import RatingStore

TABLE_NAME = os.environ.get("TA_TABLE", "tennis_alpha")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table():
    import boto3

    return boto3.resource("dynamodb").Table(TABLE_NAME)


def ratings_key(tour: str) -> dict[str, str]:
    return {"PK": f"RATINGS#{tour.upper()}", "SK": "STATE"}


def load_ratings(tour: str) -> RatingStore:
    item = _table().get_item(Key=ratings_key(tour), ConsistentRead=True).get("Item")
    if not item or not item.get("blob"):
        return RatingStore()
    return RatingStore.from_dict(json.loads(str(item["blob"])))


def save_ratings(tour: str, store: RatingStore) -> None:
    _table().put_item(
        Item={
            "PK": ratings_key(tour)["PK"],
            "SK": "STATE",
            "tour": tour.lower(),
            "blob": json.dumps(store.to_dict()),
            "matches": int(store.matches),
            "players": len(store.elo.overall),
            "updated_at": _now(),
            "stack": "tennis-alpha",
        }
    )


def put_json(pk: str, sk: str, payload: dict[str, Any]) -> None:
    item = {"PK": pk, "SK": sk, "stack": "tennis-alpha", "updated_at": _now()}
    item.update(payload)
    _table().put_item(Item=item)
