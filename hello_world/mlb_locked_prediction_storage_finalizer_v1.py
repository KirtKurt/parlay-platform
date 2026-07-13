from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

VERSION = "MLB-LOCKED-PREDICTION-STORAGE-FINALIZER-v1-exact-vector-only"


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


def _locked(result: Dict[str, Any]) -> bool:
    slate_lock = result.get("slatePredictionLock") or {}
    if slate_lock.get("locked") is True:
        return True
    return any(
        bool(
            row.get("lockedPrediction") is True
            or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
            or (row.get("lastPossiblePredictionGate") or {}).get("finalLocked") is True
        )
        for row in (result.get("predictions") or [])
        if isinstance(row, dict)
    )


def _prepare_locked_result(result: Dict[str, Any]) -> Dict[str, Any]:
    import mlb_official_freeze_bridge
    import mlb_official_prediction_semantics

    mlb_official_freeze_bridge.apply(mlb_official_prediction_semantics)
    out = mlb_official_prediction_semantics.enhance_result(copy.deepcopy(result))
    coverage = copy.deepcopy(out.get("slateCoverage") or {})
    slate_lock = copy.deepcopy(out.get("slatePredictionLock") or {})
    slate = out.get("slate_date") or out.get("slateDateEt")

    for row in out.get("predictions") or []:
        if not isinstance(row, dict):
            continue
        row["slate_date"] = row.get("slate_date") or slate
        row["slateDateEt"] = row.get("slateDateEt") or slate
        row["slateCoverage"] = copy.deepcopy(row.get("slateCoverage") or coverage)
        row["slatePredictionLock"] = copy.deepcopy(row.get("slatePredictionLock") or slate_lock)
        row["lockedPrediction"] = True
        row["officialPrediction"] = True
        row["officialPick"] = True
        row["officialPredictionStatus"] = "OFFICIAL_LOCKED_PREDICTION"
        row["lockedAtUtc"] = (
            row.get("lockedAtUtc")
            or (row.get("frozenFeatureVector") or {}).get("lockAtUtc")
            or slate_lock.get("lockAtUtc")
        )
        row["predictionSourcePullAt"] = (
            row.get("predictionSourcePullAt")
            or (row.get("frozenFeatureVector") or {}).get("sourcePullAtUtc")
            or slate_lock.get("latestScoringPullAt")
        )
        if row.get("lockedAmericanOdds") in (None, ""):
            row["lockedAmericanOdds"] = row.get("americanOdds")
        row["canonicalLockedStorageFinalizerVersion"] = VERSION

    # Re-freeze after the canonical row fields above are attached. This creates
    # the one vector that the immutable store and later settlement will share.
    return mlb_official_prediction_semantics.enhance_result(out)


def _validate_all(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    import mlb_daily_lock_ml_vector_preservation_patch as vector_contract

    errors: Dict[str, List[str]] = {}
    for row in rows:
        row_errors = vector_contract.validate_exact_locked_row(row)
        if row_errors:
            game_id = str(row.get("gameId") or row.get("game_id") or "unknown")
            errors[game_id] = row_errors
    return errors


def _store_final(module: Any, result: Dict[str, Any], requested: bool) -> Dict[str, Any]:
    if not requested or not isinstance(result, dict):
        return result
    rows = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    if not rows or not hasattr(module, "_store_prediction"):
        return result

    out = _prepare_locked_result(result) if _locked(result) else copy.deepcopy(result)
    rows = [row for row in (out.get("predictions") or []) if isinstance(row, dict)]

    validation_errors = _validate_all(rows) if _locked(out) else {}
    if validation_errors:
        out.update({
            "ok": False,
            "stored": False,
            "storedCount": 0,
            "canonicalLockedStoredCount": 0,
            "canonicalLockedStorageErrors": validation_errors,
            "canonicalLockedStorageVersion": VERSION,
            "operationalDefect": True,
            "allGamesPredicted": False,
        })
        return out

    stored_count = 0
    storage_errors: List[str] = []
    for row in rows:
        try:
            stored = module._store_prediction(row)
            row["canonicalLockedStore"] = stored
            if isinstance(stored, dict) and stored.get("ok"):
                stored_count += 1
            else:
                storage_errors.append(str(stored))
        except Exception as exc:
            row["canonicalLockedStoreError"] = str(exc)
            storage_errors.append(str(exc))

    expected = len(rows)
    storage_ok = stored_count == expected and not storage_errors
    out.update({
        "stored": True,
        "storedCount": stored_count,
        "canonicalLockedStoredCount": stored_count,
        "canonicalLockedStorageErrors": storage_errors,
        "canonicalLockedStorageVersion": VERSION,
        "canonicalLockedStorageComplete": storage_ok,
        "canonicalLockedStorageSuppressedEarlyWrites": True,
    })
    if _locked(out) and not storage_ok:
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
