#!/usr/bin/env python3
"""Diagnose the installed MLB scoring engine without writing production data.

Two paths are exercised:

1. ``store=False`` reproduces the public persisted-read authority.
2. ``store=True`` is run with ``_store_prediction`` temporarily replaced by a
   no-op interceptor. This activates the protected candidate-generation path
   and all of its storage-validation wrappers while preventing DynamoDB writes.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo("America/New_York")
PROOF_TYPE = "MLB_SCORING_ENGINE_READ_ONLY_DIAGNOSTIC"
VERSION = "MLB-SCORING-ENGINE-DIAGNOSTIC-v2-public-vs-protected-no-write"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "ok",
        "count",
        "gameCount",
        "pullCount",
        "rawPullCount",
        "allGamesPredicted",
        "stored",
        "storedCount",
        "preLockStoredCount",
        "preLockStorageCandidateCount",
        "preLockStorageComplete",
        "preLockStorageErrors",
        "canonicalLockedStorageCandidateCount",
        "canonicalLockedStoredCount",
        "canonicalLockedStorageComplete",
        "canonicalLockedStorageErrors",
        "canonicalLockedStorageSuppressedUnauthorizedCount",
        "operationalDefect",
        "predictionCoverageComplete",
        "displayStatusCoverageComplete",
        "readAuthority",
    )
    return {field: _plain(result.get(field)) for field in fields if field in result}


def _row_validation(engine: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    markers: Dict[str, Any] = {}
    validator = getattr(engine, "_public_prelock_markers", None)
    if callable(validator):
        try:
            markers = _plain(validator(copy.deepcopy(row)))
        except Exception as exc:
            errors.append(_error_text(exc))
    else:
        errors.append("public_prelock_marker_validator_unavailable")

    probability_errors: List[str] = []
    try:
        import mlb_prediction_probability_contract_v1 as probability_contract

        probability_errors = [str(value) for value in probability_contract.validation_errors(row)]
    except Exception as exc:
        probability_errors = [_error_text(exc)]

    signal_policy = row.get("signalPolicyV13") or {}
    per_game = row.get("perGameCanonicalLock") or {}
    return {
        "storageEligible": not errors,
        "storageValidationErrors": errors,
        "storageMarkers": markers,
        "probabilityContractErrors": probability_errors,
        "signalPolicyVersion": signal_policy.get("version") if isinstance(signal_policy, dict) else None,
        "perGameLockStatus": per_game.get("status") if isinstance(per_game, dict) else None,
        "perGameCanonical": per_game.get("canonical") if isinstance(per_game, dict) else None,
    }


def _diagnostic_rows(engine: Any, result: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int, int]:
    predictions = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    rows: List[Dict[str, Any]] = []
    validation_failure_count = 0
    probability_failure_count = 0
    for row in predictions:
        validation = _row_validation(engine, row)
        if not validation["storageEligible"]:
            validation_failure_count += 1
        if validation["probabilityContractErrors"]:
            probability_failure_count += 1
        rows.append({
            "gameId": row.get("gameId"),
            "gameIdentity": row.get("gameIdentity"),
            "officialGamePk": row.get("officialGamePk"),
            "awayTeam": row.get("awayTeam"),
            "homeTeam": row.get("homeTeam"),
            "commenceTime": row.get("commenceTime"),
            "predictedWinner": row.get("predictedWinner"),
            "predictedSide": row.get("predictedSide"),
            "score": row.get("score"),
            "winProbabilityPct": row.get("winProbabilityPct"),
            "displayPrediction": row.get("displayPrediction"),
            "lockedPrediction": row.get("lockedPrediction"),
            "officialPrediction": row.get("officialPrediction"),
            "officialPredictionStatus": row.get("officialPredictionStatus"),
            "recommendationStatus": row.get("recommendationStatus"),
            "blockedReasons": row.get("blockedReasons") or [],
            "preLockStore": _plain(row.get("preLockStore")),
            "preLockStoreError": row.get("preLockStoreError"),
            "canonicalLockedStoreSuppressed": row.get("canonicalLockedStoreSuppressed"),
            "canonicalLockedStoreSuppressionReason": row.get("canonicalLockedStoreSuppressionReason"),
            **validation,
        })
    return rows, validation_failure_count, probability_failure_count


def _candidate_generation_no_write(engine: Any, slate_date: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    intercepted: List[Dict[str, Any]] = []
    original_store = getattr(engine, "_store_prediction", None)
    if not callable(original_store):
        raise RuntimeError("engine_store_prediction_interceptor_target_unavailable")

    def no_write_store(row: Dict[str, Any]) -> Dict[str, Any]:
        intercepted.append({
            "gameId": row.get("gameId"),
            "gameIdentity": row.get("gameIdentity"),
            "officialGamePk": row.get("officialGamePk"),
            "predictedWinner": row.get("predictedWinner"),
            "predictedSide": row.get("predictedSide"),
            "lockedPrediction": row.get("lockedPrediction"),
            "officialPredictionStatus": row.get("officialPredictionStatus"),
        })
        return {
            "ok": True,
            "stored": False,
            "diagnosticNoWrite": True,
            "storageClass": "INTERCEPTED_READ_ONLY_DIAGNOSTIC",
        }

    engine._store_prediction = no_write_store
    try:
        result = engine.predict_all(slate_date, store=True, limit=500)
    finally:
        engine._store_prediction = original_store
    return _plain(result or {}), intercepted


def build_report(slate_date: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    runtime_status: Dict[str, Any]
    try:
        import mlb_ml_runtime_install_v3

        runtime_status = _plain(mlb_ml_runtime_install_v3.install())
    except Exception as exc:
        runtime_status = {"ok": False, "applied": False, "errors": [_error_text(exc)]}

    try:
        import mlb_game_winner_engine as engine
    except Exception as exc:
        return {
            "ok": False,
            "proofType": PROOF_TYPE,
            "version": VERSION,
            "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
            "createdAtEt": now.astimezone(SLATE_TZ).isoformat(),
            "slateDateEt": slate_date,
            "readOnly": True,
            "runtimeInstall": runtime_status,
            "engineImportError": _error_text(exc),
            "secretExposed": False,
        }

    public_result: Dict[str, Any] = {}
    public_error: Optional[str] = None
    try:
        public_result = _plain(engine.predict_all(slate_date, store=False, limit=500) or {})
    except Exception as exc:
        public_error = _error_text(exc)

    candidate_result: Dict[str, Any] = {}
    candidate_error: Optional[str] = None
    intercepted: List[Dict[str, Any]] = []
    try:
        candidate_result, intercepted = _candidate_generation_no_write(engine, slate_date)
    except Exception as exc:
        candidate_error = _error_text(exc)

    rows, validation_failures, probability_failures = _diagnostic_rows(engine, candidate_result)
    public_rows = [row for row in (public_result.get("predictions") or []) if isinstance(row, dict)]
    return {
        "ok": candidate_error is None and bool(candidate_result.get("ok")) and len(rows) > 0,
        "proofType": PROOF_TYPE,
        "version": VERSION,
        "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
        "createdAtEt": now.astimezone(SLATE_TZ).isoformat(),
        "slateDateEt": slate_date,
        "readOnly": True,
        "writePrevention": {
            "method": "temporarily_replace_engine__store_prediction_with_no_op",
            "interceptedStoreAttemptCount": len(intercepted),
            "interceptedRows": intercepted,
            "productionWritesPerformed": False,
        },
        "runtimeInstall": runtime_status,
        "engine": getattr(engine, "ENGINE", None),
        "modelVersion": getattr(engine, "MODEL_VERSION", None),
        "runtimePatchVersions": {
            key: value
            for key, value in vars(engine).items()
            if key.startswith("MLB_") and key.endswith("_VERSION") and isinstance(value, (str, int, float, bool))
        },
        "publicPersistedRead": {
            "error": public_error,
            "summary": _summary(public_result),
            "predictionCount": len(public_rows),
        },
        "protectedCandidateGenerationNoWrite": {
            "error": candidate_error,
            "summary": _summary(candidate_result),
            "calculatedPredictionCount": len(rows),
            "storageValidationPassCount": len(rows) - validation_failures,
            "storageValidationFailureCount": validation_failures,
            "probabilityContractFailureCount": probability_failures,
            "rows": rows,
        },
        "secretExposed": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-date", default=datetime.now(SLATE_TZ).date().isoformat())
    parser.add_argument("--output", default="runtime_reports/mlb_scoring_engine_diagnostic_latest.json")
    args = parser.parse_args(argv)
    report = build_report(args.slate_date)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    candidate = report.get("protectedCandidateGenerationNoWrite") or {}
    print(json.dumps({
        "ok": report.get("ok"),
        "publicPersistedPredictionCount": (report.get("publicPersistedRead") or {}).get("predictionCount"),
        "candidateCalculatedPredictionCount": candidate.get("calculatedPredictionCount"),
        "candidateStorageValidationPassCount": candidate.get("storageValidationPassCount"),
        "candidateStorageValidationFailureCount": candidate.get("storageValidationFailureCount"),
        "interceptedStoreAttemptCount": (report.get("writePrevention") or {}).get("interceptedStoreAttemptCount"),
        "output": str(path),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
