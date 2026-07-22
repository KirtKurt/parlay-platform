from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, List, Tuple

VERSION = "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v5-lifecycle-aware"
UNAUTHORIZED_LOCKED_WRITE = "immutable_per_game_stage_authority_missing"
LIFECYCLE_ONLY_STATUSES = frozenset({
    "LOCKED_NO_PREDICTION_DATA",
    "LOCK_DUE_CANONICAL_MISSING",
    "MISSED_LOCK",
    "POSTPONED",
    "CANCELLED",
    "CANCELED",
})
LIFECYCLE_ONLY_DISPLAY_GROUPS = frozenset({
    "lock_failure",
    "lock_outcome_no_prediction_data",
})
_STORAGE_REQUEST_ACTIVE: ContextVar[bool] = ContextVar(
    "inqsi_mlb_prediction_storage_request_active",
    default=False,
)


def storage_request_active() -> bool:
    return _STORAGE_REQUEST_ACTIVE.get()


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


def _lifecycle_statuses(row: Dict[str, Any]) -> List[str]:
    per_game = row.get("perGameCanonicalLock") or {}
    values = (
        row.get("lockStatus"),
        row.get("officialPredictionStatus"),
        row.get("recommendationStatus"),
        per_game.get("status") if isinstance(per_game, dict) else None,
    )
    return sorted({str(value).strip().upper() for value in values if value not in (None, "")})


def _lifecycle_only_storage_row(row: Dict[str, Any]) -> bool:
    """Return True when the row is status evidence, not a pre-lock prediction.

    Once a game's immutable cutoff has passed, public authority may return a
    ``MISSED_LOCK`` or terminal ``LOCKED_NO_PREDICTION_DATA`` row. Those rows
    must remain visible, but they must never be sent to the pre-lock prediction
    writer or counted as failed candidate persistence.
    """

    statuses = set(_lifecycle_statuses(row))
    display_group = str(row.get("displayGroup") or "").strip().lower()
    return bool(
        statuses & LIFECYCLE_ONLY_STATUSES
        or display_group in LIFECYCLE_ONLY_DISPLAY_GROUPS
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
    # semantics enhancer here. That enhancer can mark an entire mixed result
    # locked from a slate-level flag. Storage authority belongs to each row:
    # only the immutable per-game stage may create a canonical LOCKED#GAME row.
    out = result
    stored_count = 0
    pre_lock_stored_count = 0
    pre_lock_candidate_count = 0
    lifecycle_skipped_count = 0
    lifecycle_skipped_statuses: set[str] = set()
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
        elif _lifecycle_only_storage_row(row):
            lifecycle_skipped_count += 1
            statuses = _lifecycle_statuses(row)
            lifecycle_skipped_statuses.update(statuses)
            row["preLockStoreSkipped"] = True
            row["preLockStoreSkipReason"] = "post_cutoff_lifecycle_status_not_a_prediction_candidate"
            row["preLockStoreSkippedStatuses"] = statuses
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
    storage_disposition_count = (
        pre_lock_candidate_count
        + lifecycle_skipped_count
        + canonical_candidate_count
        + suppressed_locked_count
    )
    storage_disposition_complete = storage_disposition_count == len(rows)
    out.update({
        "stored": stored_count > 0,
        "storedCount": stored_count,
        "preLockStoredCount": pre_lock_stored_count,
        "preLockStorageCandidateCount": pre_lock_candidate_count,
        "preLockStorageComplete": pre_lock_storage_complete,
        "preLockStorageErrors": pre_lock_storage_errors,
        "preLockStorageLifecycleAware": True,
        "preLockStorageLifecycleSkippedCount": lifecycle_skipped_count,
        "preLockStorageLifecycleSkippedStatuses": sorted(lifecycle_skipped_statuses),
        "preLockStorageDispositionCount": storage_disposition_count,
        "preLockStorageRowCount": len(rows),
        "preLockStorageDispositionComplete": storage_disposition_complete,
        "canonicalLockedStorageCandidateCount": canonical_candidate_count,
        "canonicalLockedStoredCount": canonical_stored_count,
        "canonicalLockedStorageErrors": canonical_storage_errors,
        "canonicalLockedStorageVersion": VERSION,
        "canonicalLockedStorageComplete": canonical_storage_complete,
        "canonicalLockedStorageSuppressedUnauthorizedCount": suppressed_locked_count,
        "canonicalLockedStorageAuthority": "consistent-read verified immutable T-minus-45 stage",
        "canonicalLockedStorageSuppressedEarlyWrites": True,
    })
    if not storage_disposition_complete:
        out["ok"] = False
        out["operationalDefect"] = True
        out["allGamesPredicted"] = False
    if canonical_candidate_count and not canonical_storage_complete:
        out["ok"] = False
        out["operationalDefect"] = True
        out["allGamesPredicted"] = False
    if pre_lock_candidate_count and not pre_lock_storage_complete:
        # The lock can only promote a prediction that was durably persisted
        # before cutoff. Never report a successful HOT candidate run when one
        # or more open pre-lock rows failed storage.
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
        token = _STORAGE_REQUEST_ACTIVE.set(requested)
        try:
            result = original(*inner_args, **inner_kwargs)
        finally:
            _STORAGE_REQUEST_ACTIVE.reset(token)
        return _store_final(module, result, requested)

    module.predict_all = patched_predict_all
    module.MLB_LOCKED_STORAGE_FINALIZER_VERSION = VERSION
    module._INQSI_MLB_LOCKED_STORAGE_FINALIZER_V1_APPLIED = True
    return module
