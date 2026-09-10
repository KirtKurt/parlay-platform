from __future__ import annotations

import base64
import gzip
import json
import os
from datetime import datetime, timezone
from typing import Any

from ratings import RatingStore

TABLE_NAME = os.environ.get("TA_TABLE", "tennis_alpha")
PART_CHARS = 300_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table():
    import boto3

    return boto3.resource("dynamodb").Table(TABLE_NAME)


def ratings_key(tour: str, sk: str = "META") -> dict[str, str]:
    return {"PK": f"RATINGS#{tour.upper()}", "SK": sk}


def _slim(payload: dict[str, Any]) -> dict[str, Any]:
    h2h = {}
    for key, vals in (payload.get("h2h") or {}).items():
        trimmed = list(vals)[-8:]
        if len(trimmed) >= 2:
            h2h[key] = trimmed
    payload = dict(payload)
    payload["h2h"] = h2h
    return payload


def encode_ratings(store: RatingStore) -> str:
    raw = json.dumps(_slim(store.to_dict()), separators=(",", ":")).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, compresslevel=6)).decode("ascii")


def decode_ratings(blob: str) -> RatingStore:
    raw = gzip.decompress(base64.b64decode(blob))
    return RatingStore.from_dict(json.loads(raw.decode("utf-8")))


def load_ratings(tour: str) -> RatingStore:
    table = _table()
    meta = table.get_item(Key=ratings_key(tour, "META"), ConsistentRead=True).get("Item")
    if meta and meta.get("parts"):
        chunks = []
        for idx in range(int(meta["parts"])):
            part = table.get_item(Key=ratings_key(tour, f"PART#{idx:03d}"), ConsistentRead=True).get("Item")
            if part and part.get("blob"):
                chunks.append(str(part["blob"]))
        if chunks:
            return decode_ratings("".join(chunks))
    legacy = table.get_item(Key=ratings_key(tour, "STATE"), ConsistentRead=True).get("Item")
    if legacy and legacy.get("blob"):
        blob = str(legacy["blob"])
        try:
            return decode_ratings(blob)
        except Exception:
            return RatingStore.from_dict(json.loads(blob))
    return RatingStore()


def save_ratings(tour: str, store: RatingStore) -> None:
    table = _table()
    blob = encode_ratings(store)
    parts = [blob[i : i + PART_CHARS] for i in range(0, max(len(blob), 1), PART_CHARS)]
    table.put_item(
        Item={
            "PK": ratings_key(tour)["PK"],
            "SK": "META",
            "tour": tour.lower(),
            "parts": len(parts),
            "matches": int(store.matches),
            "players": len(store.elo.overall),
            "updated_at": _now(),
            "stack": "tennis-alpha",
        }
    )
    for idx, chunk in enumerate(parts):
        table.put_item(
            Item={
                "PK": ratings_key(tour)["PK"],
                "SK": f"PART#{idx:03d}",
                "blob": chunk,
                "stack": "tennis-alpha",
            }
        )


def put_json(pk: str, sk: str, payload: dict[str, Any]) -> None:
    item = {"PK": pk, "SK": sk, "stack": "tennis-alpha", "updated_at": _now()}
    item.update(payload)
    _table().put_item(Item=item)
