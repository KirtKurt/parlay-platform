from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_aws_training_v1 as trainer
import mlb_ml_experiment_v2 as experiment


V2_TRAINING_STATUS_MAX_AGE_MINUTES = 8.0 * 60.0
V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES = 45.0


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


def _mode_status_health(
    value: Any,
    *,
    expected_mode: str,
    maximum_age_minutes: float,
    now_utc: datetime,
    current_manifest_digest: Any,
    deployed_identity: Any,
    manifest_read_stable: bool,
) -> Dict[str, Any]:
    health = value if isinstance(value, dict) else {}
    latest = health.get("latestRun") if isinstance(health.get("latestRun"), dict) else {}
    created_at = _parse_dt(latest.get("createdAtUtc"))
    age_minutes = (
        round((now_utc - created_at).total_seconds() / 60.0, 2)
        if created_at
        else None
    )
    deployment = latest.get("deploymentIdentity") or {}
    deployment_valid = bool(deployment and deployment == deployed_identity)
    fresh = bool(
        age_minutes is not None
        and 0 <= age_minutes <= maximum_age_minutes
    )
    errors = list(health.get("errors") or [])
    contract_errors = []
    if latest.get("ok") is not True:
        contract_errors.append("status_not_ok")
    if latest.get("version") != trainer.VERSION:
        contract_errors.append("status_version_mismatch")
    if latest.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
        contract_errors.append("status_experiment_mismatch")
    if latest.get("executionMode") != expected_mode:
        contract_errors.append("status_mode_mismatch")
    if latest.get("statusFingerprintVersion") != trainer.STATUS_FINGERPRINT_VERSION:
        contract_errors.append("status_fingerprint_version_mismatch")
    if latest.get("statusFingerprint") != trainer._status_fingerprint(latest):
        contract_errors.append("status_fingerprint_mismatch")
    if not current_manifest_digest or latest.get("manifestDigest") != current_manifest_digest:
        contract_errors.append("status_manifest_mismatch")
    if not manifest_read_stable:
        contract_errors.append("manifest_changed_during_status_read")
    if not deployment_valid:
        contract_errors.append("status_deployment_identity_mismatch")
    valid = bool(
        health.get("ok") is True
        and health.get("statusPresent") is True
        and health.get("executionMode") == expected_mode
        and health.get("latestRunTimestampValid") is True
        and health.get("latestRunFresh") is True
        and not errors
        and not contract_errors
        and deployment_valid
        and fresh
    )
    return {
        "ok": valid,
        "executionMode": expected_mode,
        "statusPresent": health.get("statusPresent") is True,
        "latestRunStatus": latest.get("status"),
        "latestRunCreatedAtUtc": latest.get("createdAtUtc"),
        "latestRunTimestampValid": created_at is not None,
        "latestRunAgeMinutes": age_minutes,
        "latestRunFresh": fresh,
        "latestRunMaxAgeMinutes": maximum_age_minutes,
        "deploymentIdentity": deployment if isinstance(deployment, dict) else {},
        "deploymentIdentityValid": deployment_valid,
        "auditReportedErrors": errors,
        "contractErrors": contract_errors,
        "statusVersion": latest.get("version"),
        "experimentId": latest.get("experimentId"),
        "manifestDigest": latest.get("manifestDigest"),
        "statusFingerprintVersion": latest.get("statusFingerprintVersion"),
    }


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
    raw_v2_training = audit.get("mlTrainingV2")
    v2_training = raw_v2_training if isinstance(raw_v2_training, dict) else {}
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
        infrastructure_blockers.append(
            "AUTHORITATIVE_OFFICIAL_ACCURACY_MISSING_FOR_GRADED_ROWS"
        )
    elif official_accuracy >= target:
        accuracy_status = "TARGET_MET"
    else:
        accuracy_status = "BELOW_TARGET"
        warnings.append("ASPIRATIONAL_90_PCT_DASHBOARD_TARGET_NOT_MET")

    clean_rows = optimization.get("cleanRowCount")
    quarantined_rows = optimization.get("quarantinedRowCount")
    disposition_complete = isinstance(clean_rows, int) and isinstance(quarantined_rows, int)
    if not disposition_complete:
        infrastructure_blockers.append("ML_CLEAN_QUARANTINE_DISPOSITION_MISSING")
    elif clean_rows < 500:
        warnings.append("ML_PROMOTION_REMAINS_UNPROVEN_BELOW_500_CLEAN_ROWS")
    if not v2_training or v2_training.get("ok") is not True:
        infrastructure_blockers.append("AWS_V2_TRAINER_STATUS_MISSING_OR_INVALID")
    current_manifest_digest = v2_training.get("manifestDigest")
    manifest_read_before = v2_training.get("manifestReadBefore")
    manifest_read_after = v2_training.get("manifestReadAfter")
    manifest_read_stable = bool(
        isinstance(manifest_read_before, dict)
        and isinstance(manifest_read_after, dict)
        and manifest_read_before == manifest_read_after
        and manifest_read_after.get("manifestDigest") == current_manifest_digest
        and manifest_read_after.get("revision") is not None
        and v2_training.get("manifestReadStable") is True
    )
    deployed_identity = v2_training.get("deployedTrainerIdentity")
    deployed_identity_valid = bool(
        isinstance(deployed_identity, dict)
        and re.fullmatch(r"[0-9a-f]{40}", str(deployed_identity.get("gitSha") or ""))
        and re.fullmatch(
            r"[0-9a-f]{64}", str(deployed_identity.get("templateSha256") or "")
        )
    )
    if v2_training.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_IDENTITY_INVALID")
    if not deployed_identity_valid:
        infrastructure_blockers.append("AWS_V2_DEPLOYED_TRAINER_IDENTITY_INVALID")
    if not manifest_read_stable:
        infrastructure_blockers.append("AWS_V2_MANIFEST_STATUS_SNAPSHOT_UNSTABLE")
    training_health = _mode_status_health(
        v2_training.get("trainingHealth"),
        expected_mode="training",
        maximum_age_minutes=V2_TRAINING_STATUS_MAX_AGE_MINUTES,
        now_utc=now_utc,
        current_manifest_digest=current_manifest_digest,
        deployed_identity=deployed_identity,
        manifest_read_stable=manifest_read_stable,
    )
    capture_health = _mode_status_health(
        v2_training.get("selectionCaptureHealth"),
        expected_mode="selection_capture",
        maximum_age_minutes=V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES,
        now_utc=now_utc,
        current_manifest_digest=current_manifest_digest,
        deployed_identity=deployed_identity,
        manifest_read_stable=manifest_read_stable,
    )
    if not training_health["ok"]:
        infrastructure_blockers.append("AWS_V2_TRAINING_STATUS_MISSING_STALE_OR_INVALID")
    if not capture_health["ok"]:
        infrastructure_blockers.append(
            "AWS_V2_SELECTION_CAPTURE_STATUS_MISSING_STALE_OR_INVALID"
        )
    deployment_identity_agreement = bool(
        v2_training.get("deploymentIdentityAgreement") is True
        and training_health["deploymentIdentity"]
        and training_health["deploymentIdentity"]
        == capture_health["deploymentIdentity"]
    )
    if not deployment_identity_agreement:
        infrastructure_blockers.append("AWS_V2_MODE_DEPLOYMENT_IDENTITY_MISMATCH")
    if v2_training.get("manifestPresent") is not True:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_MANIFEST_NOT_INITIALIZED")
    elif v2_training.get("manifestValid") is not True:
        infrastructure_blockers.append("AWS_V2_EXPERIMENT_MANIFEST_INVALID")
    if v2_training.get("automaticPromotionEnabled") is True:
        infrastructure_blockers.append("AWS_V2_AUTOMATIC_FIRST_PROMOTION_ENABLED")
    elif v2_training.get("automaticPromotionEnabled") is not False:
        infrastructure_blockers.append("AWS_V2_AUTOMATIC_PROMOTION_STATE_MISSING")
    v2_counts = v2_training.get("partitionCounts") or {}
    v2_train = _int(v2_counts.get("train"))
    v2_validation = _int(v2_counts.get("validation"))
    v2_prospective = _int(v2_counts.get("prospectiveTest"))
    v2_total = v2_train + v2_validation + v2_prospective
    v2_milestones = v2_training.get("milestones") or {}
    v2_milestone_counts = v2_milestones.get("counts") or {}
    v2_selected = _int(
        v2_milestone_counts.get("settledProspectiveSelectedRecommendations")
    )
    candidate_decision = v2_training.get("candidatePromotionDecision")
    champion_present = v2_training.get("championPresent") is True

    infrastructure_blockers = sorted(set(infrastructure_blockers))
    model_blockers = sorted(set(model_blockers))
    warnings = sorted(set(warnings))
    infrastructure_ok = not infrastructure_blockers
    accuracy_target_met = accuracy_status == "TARGET_MET"
    # A one-day or rolling 90% result is an aspirational dashboard metric, not
    # a production-health or ML-promotion gate. Integrity failures still stop
    # acceptance; ordinary predictive variance does not.
    overall_ok = infrastructure_ok

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
            "legacyV1AuthorityEnabled": False,
            "v2PromotionPolicy": "fixed_300_100_100_prospective_manual_first",
            "v2Training": {
                "manifestReadStable": manifest_read_stable,
                "manifestReadBefore": (
                    manifest_read_before
                    if isinstance(manifest_read_before, dict)
                    else {}
                ),
                "manifestReadAfter": (
                    manifest_read_after
                    if isinstance(manifest_read_after, dict)
                    else {}
                ),
                "statusPresent": training_health["statusPresent"],
                "latestRunStatus": training_health["latestRunStatus"],
                "latestRunCreatedAtUtc": training_health[
                    "latestRunCreatedAtUtc"
                ],
                "latestRunTimestampValid": training_health[
                    "latestRunTimestampValid"
                ],
                "latestRunAgeMinutes": training_health["latestRunAgeMinutes"],
                "latestRunFresh": training_health["latestRunFresh"],
                "latestRunMaxAgeMinutes": training_health[
                    "latestRunMaxAgeMinutes"
                ],
                "trainingHealth": training_health,
                "selectionCaptureHealth": capture_health,
                "deploymentIdentityAgreement": deployment_identity_agreement,
                "manifestPresent": v2_training.get("manifestPresent"),
                "manifestValid": v2_training.get("manifestValid"),
                "manifestPhase": v2_training.get("manifestPhase"),
                "partitionCounts": {
                    "train": v2_train,
                    "validation": v2_validation,
                    "prospectiveTest": v2_prospective,
                },
                "totalRows": v2_total,
                "settledProspectiveSelectedRecommendations": v2_selected,
                "milestoneStage": v2_milestones.get("stage"),
                "projectedFullCleanSlatesRemaining": v2_milestones.get(
                    "projectedFullCleanSlatesRemaining"
                ),
                "candidatePromotionDecision": candidate_decision,
                "championPresent": champion_present,
                "firstPromotionRequiresManualReview": True,
                "automaticPromotionEnabled": v2_training.get(
                    "automaticPromotionEnabled"
                ),
            },
        },
        "infrastructureBlockers": infrastructure_blockers,
        "modelBlockers": model_blockers,
        "warnings": warnings,
        "unproven": [
            item
            for item, condition in [
                ("one complete uncontaminated live slate", not infrastructure_ok),
                ("500 clean V2 rows across fixed 300/100/100 partitions", v2_total < 500),
                ("100 sealed prospective-test rows", v2_prospective < 100),
                (
                    "100 prospectively selected recommendations for playability",
                    v2_selected < 100,
                ),
                ("first V2 champion manual review", not champion_present),
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
