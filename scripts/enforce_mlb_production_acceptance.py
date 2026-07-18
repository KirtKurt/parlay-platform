from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return payload


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def build_acceptance(
    *,
    pull_guard: Dict[str, Any],
    verifier: Dict[str, Any],
    audit: Dict[str, Any],
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    infrastructure_blockers: List[str] = []
    model_blockers: List[str] = []
    warnings: List[str] = []

    if pull_guard.get("guardPassed") is not True:
        infrastructure_blockers.append("PULL_GUARD_FAILED")
    if pull_guard.get("officialScheduleVerified") is not True:
        infrastructure_blockers.append("OFFICIAL_SCHEDULE_UNVERIFIED")
    if pull_guard.get("pullsRequired") is True and pull_guard.get("fresh") is not True:
        infrastructure_blockers.append("LATEST_PULL_NOT_FRESH")
    if pull_guard.get("missingCleanScheduledSlots"):
        infrastructure_blockers.append("MISSING_15_MINUTE_PULL_SLOTS")
    if _int(pull_guard.get("duplicateOrExtraPullsSinceStart")) > 0:
        infrastructure_blockers.append("DUPLICATE_OR_EXTRA_SCHEDULED_PULLS")
    if _int(pull_guard.get("preStartPollutedPullCount")) > 0:
        infrastructure_blockers.append("PRESTART_PULLS_EXIST_ON_CURRENT_SLATE")

    if verifier.get("ok") is not True:
        infrastructure_blockers.append("LIVE_PRODUCTION_VERIFIER_FAILED")
    for blocker in verifier.get("blockers") or []:
        infrastructure_blockers.append(f"VERIFIER:{blocker}")

    summary = audit.get("summary") or {}
    optimization = audit.get("mlOptimizationV3") or {}
    if audit.get("ok") is not True:
        infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_FAILED")
    audit_created = _parse_dt(audit.get("createdAtUtc"))
    audit_age_minutes = None
    if audit_created:
        audit_age_minutes = round((now_utc - audit_created).total_seconds() / 60.0, 2)
        if audit_age_minutes < 0 or audit_age_minutes > 45:
            infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_STALE")
    else:
        infrastructure_blockers.append("AWS_BACKED_ML_AUDIT_TIMESTAMP_MISSING")

    completed = _int(summary.get("completedFinalGames"))
    graded = _int(summary.get("gradedPredictionCount"))
    missing = _int(summary.get("missingPredictionCount"))
    official_count = _int(summary.get("officialPredictionCount"))
    official_accuracy = _float(summary.get("rolling24hOfficialAccuracyPct"))
    all_games_accuracy = _float(summary.get("rolling24hAllGamesAccuracyPct"))
    target = _float(summary.get("targetAccuracyPct")) or 90.0

    if completed > graded or missing > 0:
        infrastructure_blockers.append("COMPLETED_GAMES_WITHOUT_IMMUTABLE_GRADEABLE_PREDICTIONS")
    if completed > 0 and official_count == 0:
        infrastructure_blockers.append("NO_OFFICIAL_PREDICTIONS_FOR_COMPLETED_WINDOW")

    if graded == 0:
        accuracy_status = "UNMEASURABLE_NO_GRADED_PREDICTIONS"
    elif official_accuracy is None:
        accuracy_status = "UNMEASURABLE_OFFICIAL_ACCURACY_MISSING"
        model_blockers.append("OFFICIAL_ACCURACY_MISSING")
    elif official_accuracy >= target:
        accuracy_status = "TARGET_MET"
    else:
        accuracy_status = "BELOW_TARGET"
        model_blockers.append("ROLLING_24H_OFFICIAL_ACCURACY_BELOW_90_PCT")

    clean_rows = optimization.get("cleanRowCount")
    quarantined_rows = optimization.get("quarantinedRowCount")
    disposition_complete = isinstance(clean_rows, int) and isinstance(quarantined_rows, int)
    if not disposition_complete:
        infrastructure_blockers.append("ML_CLEAN_QUARANTINE_DISPOSITION_MISSING")
    elif clean_rows < 500:
        warnings.append("ML_PROMOTION_REMAINS_UNPROVEN_BELOW_500_CLEAN_ROWS")

    infrastructure_blockers = sorted(set(infrastructure_blockers))
    model_blockers = sorted(set(model_blockers))
    warnings = sorted(set(warnings))
    infrastructure_ok = not infrastructure_blockers
    accuracy_target_met = accuracy_status == "TARGET_MET"
    overall_ok = infrastructure_ok and (graded == 0 or accuracy_target_met)

    return {
        "ok": overall_ok,
        "proofType": "INQSI_MLB_END_TO_END_PRODUCTION_ACCEPTANCE",
        "createdAtUtc": now_utc.isoformat(),
        "slateDateEt": pull_guard.get("slateDateEt") or verifier.get("slateDateEt"),
        "infrastructureOk": infrastructure_ok,
        "accuracyStatus": accuracy_status,
        "accuracyTargetPct": target,
        "accuracyTargetMet": accuracy_target_met if graded > 0 else None,
        "signalTuningFrozen": not infrastructure_ok,
        "pullCoverage": {
            "officialGameCount": pull_guard.get("officialGameCount"),
            "cleanExpectedPullCount": pull_guard.get("cleanExpectedPullCount"),
            "cleanActualScheduledSlotCount": pull_guard.get("cleanActualScheduledSlotCount"),
            "missingSlots": pull_guard.get("missingCleanScheduledSlots") or [],
            "duplicateOrExtraPulls": pull_guard.get("duplicateOrExtraPullsSinceStart"),
            "preStartPollutedPullCount": pull_guard.get("preStartPollutedPullCount"),
            "latestPullAgeMinutes": pull_guard.get("latestRawPullAgeMinutes"),
            "guardPassed": pull_guard.get("guardPassed"),
        },
        "predictionAndLock": {
            "gameCount": verifier.get("gameCount"),
            "predictionCount": verifier.get("predictionCount"),
            "allGamesPredicted": verifier.get("allGamesPredicted"),
            "lock": verifier.get("lock"),
            "lockedRowIntegrity": verifier.get("lockedRowIntegrity"),
            "verifierOk": verifier.get("ok"),
        },
        "settlementAndAccuracy": {
            "completedFinalGames": completed,
            "gradedPredictionCount": graded,
            "missingPredictionCount": missing,
            "officialPredictionCount": official_count,
            "rolling24hOfficialAccuracyPct": official_accuracy,
            "rolling24hAllGamesAccuracyPct": all_games_accuracy,
            "auditAgeMinutes": audit_age_minutes,
        },
        "mlDisposition": {
            "cleanRowCount": clean_rows,
            "quarantinedRowCount": quarantined_rows,
            "dispositionComplete": disposition_complete,
            "mode": optimization.get("mode"),
            "automaticPromotionEnabled": optimization.get("automaticPromotionEnabled"),
        },
        "infrastructureBlockers": infrastructure_blockers,
        "modelBlockers": model_blockers,
        "warnings": warnings,
        "unproven": [
            item
            for item, condition in [
                ("one complete uncontaminated live slate", not infrastructure_ok),
                ("rolling 24-hour accuracy at or above 90%", not accuracy_target_met),
                ("ML automatic authority with at least 500 clean rows", not isinstance(clean_rows, int) or clean_rows < 500),
            ]
            if condition
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull-guard", required=True, type=Path)
    parser.add_argument("--verifier", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    result = build_acceptance(
        pull_guard=_load(args.pull_guard),
        verifier=_load(args.verifier),
        audit=_load(args.audit),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    if result.get("ok") is not True:
        raise SystemExit(
            "MLB production acceptance failed: "
            + json.dumps({
                "infrastructureBlockers": result.get("infrastructureBlockers"),
                "modelBlockers": result.get("modelBlockers"),
                "unproven": result.get("unproven"),
            }, default=str)
        )


if __name__ == "__main__":
    main()
