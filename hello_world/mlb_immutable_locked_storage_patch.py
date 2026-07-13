from __future__ import annotations

import copy
from typing import Any, Dict, List

from botocore.exceptions import ClientError

VERSION = "MLB-IMMUTABLE-LOCKED-STORAGE-v2-exact-vector-write-once"


def _tags(row: Dict[str, Any]) -> set[str]:
    return {str(value) for value in (row.get("tags") or [])}


def _locked(row: Dict[str, Any]) -> bool:
    tags = _tags(row)
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    audit = row.get("lockedCardAudit") or {}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or audit.get("lockedFlag") is True
        or (isinstance(lock, dict) and lock.get("locked") is True)
        or "FINAL_LOCKED" in tags
        or "SLATE_LOCKED" in tags
        or "OFFICIAL_LOCKED_PREDICTION" in tags
    )


def _slate(row: Dict[str, Any]) -> str:
    return str(row.get("slate_date") or row.get("slateDateEt") or "unknown")


def _identity(row: Dict[str, Any]) -> str:
    return str(row.get("gameIdentity") or row.get("gameId") or row.get("game_id") or row.get("id") or "unknown")


def _commence(row: Dict[str, Any]) -> str:
    return str(row.get("commenceTime") or row.get("commence_time") or "unknown")


def _locked_item(module: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    row["immutableLockedStorageVersion"] = VERSION
    row["immutableLockedStorage"] = True
    row["immutableLockedStorageKeyspace"] = "LOCKED#GAME"
    return module.history.ddb_safe({
        "PK": f"GAME_WINNERS#mlb#{_slate(row)}",
        "SK": f"LOCKED#GAME#{_commence(row)}#{_identity(row)}",
        "record_type": "mlb_immutable_locked_single_game_prediction",
        "sport": "mlb",
        "slate_date": _slate(row),
        "game_id": row.get("gameId") or row.get("game_id") or row.get("id"),
        "game_identity": row.get("gameIdentity") or _identity(row),
        "game_key": row.get("gameKey"),
        "predicted_winner": row.get("predictedWinner"),
        "confidence_tier": row.get("confidenceTier"),
        "promotion_status": row.get("promotionStatus"),
        "promoted": row.get("promoted"),
        "score": row.get("score"),
        "win_probability": row.get("winProbability"),
        "edge_vs_book": row.get("edgeVsBook"),
        "expected_value": row.get("expectedValue"),
        "created_at": row.get("createdAt") or row.get("created_at"),
        "immutable_locked": True,
        "immutable_locked_storage_version": VERSION,
        "data": row,
    })


def _vector_errors(row: Dict[str, Any]) -> List[str]:
    try:
        import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
        return vector_contract.validate_exact_locked_row(row)
    except Exception as exc:
        return [f"exact_vector_validator_unavailable:{exc}"]


def _require_vector(row: Dict[str, Any], *, context: str) -> None:
    errors = _vector_errors(row)
    if errors:
        game_id = _identity(row)
        raise RuntimeError(
            f"MLB_IMMUTABLE_LOCKED_VECTOR_REJECTED:{context}:{game_id}:"
            + ",".join(sorted(set(errors)))
        )


def _stored_row(item: Dict[str, Any]) -> Dict[str, Any]:
    data = item.get("data") or {}
    return copy.deepcopy(data) if isinstance(data, dict) else {}


def _fingerprint(row: Dict[str, Any]) -> str:
    vector = row.get("frozenFeatureVector") or {}
    return str(vector.get("fingerprint") or "")


def _vector_identity(row: Dict[str, Any]) -> Dict[str, Any]:
    vector = row.get("frozenFeatureVector") or {}
    return {
        "version": vector.get("version"),
        "fingerprint": vector.get("fingerprint"),
        "gameId": vector.get("gameId"),
        "lockAtUtc": vector.get("lockAtUtc"),
        "sourcePullAtUtc": vector.get("sourcePullAtUtc"),
        "predictedWinner": vector.get("predictedWinner"),
        "predictedSide": vector.get("predictedSide"),
        "labels": vector.get("labels") or {},
    }


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED", False):
        return module

    original_store = module._store_prediction

    def store_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
        if not _locked(row):
            stored = original_store(row)
            if isinstance(stored, dict):
                stored = dict(stored)
                stored["storageClass"] = "LIVE_MUTABLE"
                stored["immutableLockedStorageVersion"] = VERSION
            return stored

        if module.history.PULLS is None:
            return {"ok": False, "error": "SNAPSHOTS_TABLE not configured", "storageClass": "LOCKED_IMMUTABLE"}

        _require_vector(row, context="new_write")
        item = _locked_item(module, row)
        try:
            module.history.PULLS.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return {
                "ok": True,
                "pk": item["PK"],
                "sk": item["SK"],
                "storageClass": "LOCKED_IMMUTABLE",
                "writeOnce": True,
                "created": True,
                "exactVectorVerified": True,
                "frozenFeatureVectorFingerprint": _fingerprint(row),
                "version": VERSION,
            }
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            if code != "ConditionalCheckFailedException":
                raise
            existing = module.history.PULLS.get_item(
                Key={"PK": item["PK"], "SK": item["SK"]},
                ConsistentRead=True,
            ).get("Item")
            if not existing:
                raise
            existing_row = _stored_row(existing)
            _require_vector(existing_row, context="existing_collision")
            incoming_fingerprint = _fingerprint(row)
            existing_fingerprint = _fingerprint(existing_row)
            if (
                not incoming_fingerprint
                or incoming_fingerprint != existing_fingerprint
                or _vector_identity(row) != _vector_identity(existing_row)
                or row.get("predictedWinner") != existing_row.get("predictedWinner")
                or row.get("predictedSide") != existing_row.get("predictedSide")
            ):
                raise RuntimeError(
                    "MLB_IMMUTABLE_LOCKED_VECTOR_COLLISION_MISMATCH:"
                    f"{_identity(row)}:{existing_fingerprint or 'missing'}:"
                    f"{incoming_fingerprint or 'missing'}"
                )
            return {
                "ok": True,
                "pk": item["PK"],
                "sk": item["SK"],
                "storageClass": "LOCKED_IMMUTABLE",
                "writeOnce": True,
                "created": False,
                "immutableExisting": True,
                "exactVectorVerified": True,
                "frozenFeatureVectorFingerprint": existing_fingerprint,
                "version": VERSION,
            }

    module._store_prediction = store_prediction
    module.IMMUTABLE_LOCKED_STORAGE_VERSION = VERSION
    module._INQSI_MLB_IMMUTABLE_LOCKED_STORAGE_APPLIED = True
    return module
