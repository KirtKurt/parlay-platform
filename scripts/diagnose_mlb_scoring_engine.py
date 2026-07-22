#!/usr/bin/env python3
"""Run the installed MLB scoring engine read-only and explain storage eligibility.

This diagnostic intentionally calls ``predict_all(..., store=False)``. It may
read live DynamoDB state but must not write candidate or locked predictions.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo("America/New_York")
PROOF_TYPE = "MLB_SCORING_ENGINE_READ_ONLY_DIAGNOSTIC"
VERSION = "MLB-SCORING-ENGINE-DIAGNOSTIC-v1"


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

    try:
        result = _plain(engine.predict_all(slate_date, store=False, limit=500))
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
            "engine": getattr(engine, "ENGINE", None),
            "modelVersion": getattr(engine, "MODEL_VERSION", None),
            "engineRunError": _error_text(exc),
            "secretExposed": False,
        }

    predictions = [row for row in (result.get("predictions") or []) if isinstance(row, dict)]
    rows = []
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
            **validation,
        })

    summary_fields = (
        "ok",
        "count",
        "gameCount",
        "pullCount",
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
        "operationalDefect",
    )
    result_summary = {field: result.get(field) for field in summary_fields if field in result}
    return {
        "ok": bool(result.get("ok")) and len(predictions) > 0,
        "proofType": PROOF_TYPE,
        "version": VERSION,
        "createdAtUtc": now.isoformat().replace("+00:00", "Z"),
        "createdAtEt": now.astimezone(SLATE_TZ).isoformat(),
        "slateDateEt": slate_date,
        "readOnly": True,
        "runtimeInstall": runtime_status,
        "engine": getattr(engine, "ENGINE", None),
        "modelVersion": getattr(engine, "MODEL_VERSION", None),
        "runtimePatchVersions": {
            key: value
            for key, value in vars(engine).items()
            if key.startswith("MLB_") and key.endswith("_VERSION") and isinstance(value, (str, int, float, bool))
        },
        "resultSummary": result_summary,
        "calculatedPredictionCount": len(predictions),
        "storageValidationPassCount": len(predictions) - validation_failure_count,
        "storageValidationFailureCount": validation_failure_count,
        "probabilityContractFailureCount": probability_failure_count,
        "rows": rows,
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
    print(json.dumps({
        "ok": report.get("ok"),
        "calculatedPredictionCount": report.get("calculatedPredictionCount"),
        "storageValidationPassCount": report.get("storageValidationPassCount"),
        "storageValidationFailureCount": report.get("storageValidationFailureCount"),
        "resultSummary": report.get("resultSummary"),
        "output": str(path),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
