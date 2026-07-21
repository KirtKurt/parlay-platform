from __future__ import annotations

from typing import Any, Dict, List, Tuple

VERSION = "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v4-selection-vector-separated"
UNAUTHORIZED_LOCKED_WRITE = "immutable_per_game_stage_authority_missing"


def _store_requested(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> bool:
    if "store" in kwargs:
        return bool(kwargs.get("store"))
    return bool(args[1]) if len(args) > 1 else False


def _without_store(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    inner_args = list(args)
    inner_kwargs = dict(kwargs)
    if "store" in inner_kwargs or len(inner_args) < 2:
        inner_kwargs["store"] = False
    else:
        inner_args[1] = False
    return tuple(inner_args), inner_kwargs


def _row_locked(row: Dict[str, Any]) -> bool:
    lock = row.get("slatePredictionLock") or row.get("lastPossiblePredictionGate") or {}
    audit = row.get("lockedCardAudit") or {}
    tags = {str(value) for value in (row.get("tags") or [])}
    return bool(
        row.get("lockedPrediction") is True
        or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        or audit.get("lockedFlag") is True
        or (isinstance(lock, dict) and (lock.get("locked") is True or lock.get("finalLocked") is True))
        or bool(tags & {"FINAL_LOCKED", "SLATE_LOCKED", "OFFICIAL_LOCKED_PREDICTION"})
    )


def _validate_row(row: Dict[str, Any]) -> List[str]:
    import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

    validator = getattr(vector_contract, "validate_selection_lock_vector_status", None)
    if not callable(validator):
        return ["selection_vector_status_validator_unavailable"]
    return validator(row)


def _game_id(row: Dict[str, Any]) -> str:
    return str(row.get("gameId") or row.get("game_id") or row.get("gameIdentity") or "unknown")


def _store_final(module: Any, result: Dict[str, Any], requested: bool) -> Dict[str, Any]:
    if not requested or not isinstance(result, dict):
        return result
    rows = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    if not rows or not hasattr(module, "_store_prediction"):
        return result

    # The result is intentionally not run back through the legacy official
    # semantics enhancer here.  That enhancer can mark an entire mixed result
    # locked from a slate-level flag.  Storage authority belongs to each row:
    # only the immutable per-game stage may create a canonical LOCKED#GAME row.
    out = result
    stored_count = 0
    pre_lock_stored_count = 0
    pre_lock_candidate_count = 0
    canonical_stored_count = 0
    canonical_candidate_count = 0
    suppressed_locked_count = 0
    pre_lock_storage_errors: List[str] = []
    canonical_storage_errors: Dict[str, List[str]] = {}
    for row in rows:
        row_locked = _row_locked(row)
        stage_authorized = row.get("immutablePerGameStage") is True

        if row_locked and not stage_authorized:
            suppressed_locked_count += 1
            row["canonicalLockedStoreSuppressed"] = True
            row["canonicalLockedStoreSuppressionReason"] = UNAUTHORIZED_LOCKED_WRITE
            continue

        if row_locked:
            canonical_candidate_count += 1
            row_errors = _validate_row(row)
            if row_errors:
                canonical_storage_errors[_game_id(row)] = sorted(set(row_errors))
                row["canonicalLockedStoreError"] = ",".join(sorted(set(row_errors)))
                continue
        else:
            pre_lock_candidate_count += 1

        try:
            stored = module._store_prediction(row)
            if row_locked:
                row["canonicalLockedStore"] = stored
            else:
                row["preLockStore"] = stored
            if isinstance(stored, dict) and stored.get("ok"):
                stored_count += 1
                if row_locked:
                    canonical_stored_count += 1
                else:
                    pre_lock_stored_count += 1
            else:
                if row_locked:
                    canonical_storage_errors.setdefault(_game_id(row), []).append(str(stored))
                else:
                    pre_lock_storage_errors.append(str(stored))
        except Exception as exc:
            if row_locked:
                row["canonicalLockedStoreError"] = str(exc)
                canonical_storage_errors.setdefault(_game_id(row), []).append(str(exc))
            else:
                row["preLockStoreError"] = str(exc)
                pre_lock_storage_errors.append(str(exc))

    canonical_storage_complete = bool(
        canonical_candidate_count
        and canonical_stored_count == canonical_candidate_count
        and not canonical_storage_errors
    )
    pre_lock_storage_complete = bool(
        pre_lock_candidate_count == pre_lock_stored_count
        and not pre_lock_storage_errors
    )
    out.update({
        "stored": stored_count > 0,
        "storedCount": stored_count,
        "preLockStoredCount": pre_lock_stored_count,
        "preLockStorageCandidateCount": pre_lock_candidate_count,
        "preLockStorageComplete": pre_lock_storage_complete,
        "preLockStorageErrors": pre_lock_storage_errors,
        "canonicalLockedStorageCandidateCount": canonical_candidate_count,
        "canonicalLockedStoredCount": canonical_stored_count,
        "canonicalLockedStorageErrors": canonical_storage_errors,
        "canonicalLockedStorageVersion": VERSION,
        "canonicalLockedStorageComplete": canonical_storage_complete,
        "canonicalLockedStorageSuppressedUnauthorizedCount": suppressed_locked_count,
        "canonicalLockedStorageAuthority": "consistent-read verified immutable T-minus-45 stage",
        "canonicalLockedStorageSuppressedEarlyWrites": True,
    })
    if canonical_candidate_count and not canonical_storage_complete:
        out["ok"] = False
        out["operationalDefect"] = True
        out["allGamesPredicted"] = False
    if pre_lock_candidate_count and not pre_lock_storage_complete:
        # The lock can only promote a prediction that was durably persisted
        # before cutoff.  Never report a successful HOT candidate run when one
        # or more pre-lock rows failed storage.
        out["ok"] = False
        out["operationalDefect"] = True
        out["allGamesPredicted"] = False
    return out


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_LOCKED_STORAGE_FINALIZER_V1_APPLIED", False):
        return module

    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        requested = _store_requested(args, kwargs)
        inner_args, inner_kwargs = _without_store(args, kwargs)
        result = original(*inner_args, **inner_kwargs)
        return _store_final(module, result, requested)

    module.predict_all = patched_predict_all
    module.MLB_LOCKED_STORAGE_FINALIZER_VERSION = VERSION
    module._INQSI_MLB_LOCKED_STORAGE_FINALIZER_V1_APPLIED = True
    return module
