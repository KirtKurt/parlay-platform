#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

REPORT_PATH = ROOT / "runtime_reports" / "mlb_ml_v3_audit_execution_latest.json"
V2_TRAINING_STATUS_MAX_AGE_MINUTES = 8.0 * 60.0
V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES = 45.0
PRODUCTION_STACK_NAME = "parlay-platform-dev"
TRAINER_LOGICAL_ID = "MLBMLTrainingFunction"


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _read_deployed_trainer_identity() -> dict:
    """Resolve the identity stamped into the Lambda that actually runs training."""
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    client_kwargs = {"region_name": region} if region else {}
    cloudformation = boto3.client("cloudformation", **client_kwargs)
    lambdas = boto3.client("lambda", **client_kwargs)
    detail = cloudformation.describe_stack_resource(
        StackName=os.environ.get("MLB_PRODUCTION_STACK_NAME", PRODUCTION_STACK_NAME),
        LogicalResourceId=TRAINER_LOGICAL_ID,
    )["StackResourceDetail"]
    function_name = str(detail.get("PhysicalResourceId") or "").strip()
    if not function_name:
        raise RuntimeError("deployed MLB trainer physical identity is missing")
    config = lambdas.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    identity = {
        "gitSha": str(environment.get("INQSI_DEPLOY_GIT_SHA") or "").strip(),
        "templateSha256": str(
            environment.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
        ).strip(),
    }
    if not (
        re.fullmatch(r"[0-9a-f]{40}", identity["gitSha"])
        and re.fullmatch(r"[0-9a-f]{64}", identity["templateSha256"])
    ):
        raise RuntimeError("deployed MLB trainer release identity is invalid")
    return identity


def _read_v2_training_state(*, now_utc=None, deployed_identity=None) -> dict:
    """Read the AWS-native V2 control records without invoking or mutating it."""
    import boto3
    import mlb_ml_experiment_v2 as experiment
    import mlb_ml_aws_training_v1 as trainer

    table_name = os.environ.get("SNAPSHOTS_TABLE", "")
    experiment_id = os.environ.get(
        "MLB_ML_EXPERIMENT_ID", "mlb-v2-2026-07-21-future-prospective-r2"
    )
    if not table_name:
        raise RuntimeError("SNAPSHOTS_TABLE is required for V2 status monitoring")
    table = boto3.resource("dynamodb").Table(table_name)

    def read(pk, sk):
        item = table.get_item(
            Key={"PK": pk, "SK": sk}, ConsistentRead=True
        ).get("Item") or {}
        data = item.get("data") or {}
        return data if isinstance(data, dict) else {}

    experiment_pk = f"MLB_ML_EXPERIMENT#V2#{experiment_id}"
    manifest_before = read(experiment_pk, "MANIFEST")
    candidate = read(experiment_pk, "CANDIDATE#LATEST")
    generic_latest_status = read(experiment_pk, "STATUS#LATEST")
    training_status = read(experiment_pk, "STATUS#LATEST#TRAINING")
    selection_capture_status = read(
        experiment_pk, "STATUS#LATEST#SELECTION_CAPTURE"
    )
    # A trainer run can advance the manifest between the first control-record
    # read and the mode-specific heartbeat reads. Re-read after both heartbeats
    # and fail closed unless the revision/digest pair is unchanged.
    manifest_after = read(experiment_pk, "MANIFEST")
    champion = read("MLB_ML_CHAMPION#V2", "ACTIVE")
    checked_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deployed_identity = deployed_identity or _read_deployed_trainer_identity()
    manifest_read_before = {
        "revision": manifest_before.get("revision"),
        "manifestDigest": manifest_before.get("manifestDigest"),
    }
    manifest_read_after = {
        "revision": manifest_after.get("revision"),
        "manifestDigest": manifest_after.get("manifestDigest"),
    }
    manifest_read_stable = bool(
        manifest_before
        and manifest_after
        and manifest_read_before == manifest_read_after
        and manifest_read_after["revision"] is not None
        and manifest_read_after["manifestDigest"]
    )
    manifest = manifest_after
    manifest_valid = bool(
        manifest
        and manifest.get("version") == experiment.VERSION
        and manifest.get("experimentId") == experiment.PRODUCTION_EXPERIMENT_ID
        and manifest.get("manifestDigest")
        == experiment.manifest_digest(manifest)
    )
    current_manifest_digest = manifest.get("manifestDigest") if manifest_valid else None

    def status_health(status, *, execution_mode, maximum_age_minutes):
        created_at = _parse_dt(status.get("createdAtUtc"))
        age_minutes = (
            round((checked_at - created_at).total_seconds() / 60.0, 2)
            if created_at
            else None
        )
        present = bool(status)
        fresh = bool(
            age_minutes is not None
            and 0 <= age_minutes <= maximum_age_minutes
        )
        errors = []
        if not present:
            errors.append("status_missing")
        if present and status.get("ok") is not True:
            errors.append("status_not_ok")
        if present and status.get("executionMode") != execution_mode:
            errors.append("status_mode_mismatch")
        if present and status.get("version") != trainer.VERSION:
            errors.append("status_version_mismatch")
        if present and status.get("experimentId") != experiment.PRODUCTION_EXPERIMENT_ID:
            errors.append("status_experiment_mismatch")
        if present and status.get("statusFingerprintVersion") != trainer.STATUS_FINGERPRINT_VERSION:
            errors.append("status_fingerprint_version_mismatch")
        if present and status.get("statusFingerprint") != trainer._status_fingerprint(status):
            errors.append("status_fingerprint_mismatch")
        if not manifest_valid:
            errors.append("current_manifest_missing_or_invalid")
        elif not manifest_read_stable:
            errors.append("manifest_changed_during_status_read")
        elif present and status.get("manifestDigest") != current_manifest_digest:
            errors.append("status_manifest_mismatch")
        if created_at is None:
            errors.append("status_timestamp_invalid")
        elif age_minutes < 0:
            errors.append("status_from_future")
        elif not fresh:
            errors.append("status_stale")
        deployment = status.get("deploymentIdentity") or {}
        deployment_matches = bool(present and deployment == deployed_identity)
        if present and not deployment_matches:
            errors.append("status_deployment_identity_mismatch")
        return {
            "ok": not errors,
            "executionMode": execution_mode,
            "statusPresent": present,
            "latestRunStatus": status.get("status"),
            "latestRunCreatedAtUtc": status.get("createdAtUtc"),
            "latestRunTimestampValid": created_at is not None,
            "latestRunAgeMinutes": age_minutes,
            "latestRunFresh": fresh,
            "latestRunMaxAgeMinutes": maximum_age_minutes,
            "deploymentIdentity": deployment,
            "deploymentIdentityMatches": deployment_matches,
            "statusVersion": status.get("version"),
            "experimentId": status.get("experimentId"),
            "manifestDigest": status.get("manifestDigest"),
            "statusFingerprintVersion": status.get("statusFingerprintVersion"),
            "latestRun": status,
            "errors": errors,
        }

    training_health = status_health(
        training_status,
        execution_mode="training",
        maximum_age_minutes=V2_TRAINING_STATUS_MAX_AGE_MINUTES,
    )
    selection_capture_health = status_health(
        selection_capture_status,
        execution_mode="selection_capture",
        maximum_age_minutes=V2_SELECTION_CAPTURE_STATUS_MAX_AGE_MINUTES,
    )
    deployment_identity_agreement = bool(
        training_health["deploymentIdentity"]
        and training_health["deploymentIdentity"]
        == selection_capture_health["deploymentIdentity"]
    )
    return {
        "ok": bool(
            training_health["ok"]
            and selection_capture_health["ok"]
            and deployment_identity_agreement
            and experiment_id == experiment.PRODUCTION_EXPERIMENT_ID
            and manifest_read_stable
        ),
        "readOnly": True,
        "experimentId": experiment_id,
        "experimentIdValid": experiment_id == experiment.PRODUCTION_EXPERIMENT_ID,
        "manifestDigest": current_manifest_digest,
        "manifestReadStable": manifest_read_stable,
        "manifestReadBefore": manifest_read_before,
        "manifestReadAfter": manifest_read_after,
        "manifestPresent": bool(manifest),
        "manifestValid": manifest_valid if manifest else None,
        "manifestPhase": manifest.get("phase"),
        "partitionCounts": {
            name: int(((manifest.get("partitions") or {}).get(name) or {}).get("rowCount") or 0)
            for name in experiment.PARTITION_ORDER
        },
        "latestCandidatePresent": bool(candidate),
        "latestCandidateArtifactDigest": candidate.get("artifactDigest"),
        "candidatePromotionDecision": (candidate.get("promotionGate") or {}).get(
            "promotionDecision"
        ),
        # Compatibility aliases deliberately describe the full training run,
        # never the more frequently overwritten generic record.
        "statusPresent": training_health["statusPresent"],
        "latestRunStatus": training_health["latestRunStatus"],
        "latestRunCreatedAtUtc": training_health["latestRunCreatedAtUtc"],
        "latestRunTimestampValid": training_health["latestRunTimestampValid"],
        "latestRunAgeMinutes": training_health["latestRunAgeMinutes"],
        "latestRunFresh": training_health["latestRunFresh"],
        "latestRunMaxAgeMinutes": training_health["latestRunMaxAgeMinutes"],
        "trainingHealth": training_health,
        "selectionCaptureHealth": selection_capture_health,
        "deploymentIdentityAgreement": deployment_identity_agreement,
        "deployedTrainerIdentity": deployed_identity,
        "genericLatestStatusDiagnosticOnly": generic_latest_status,
        "milestones": training_status.get("milestones") or {},
        "championPresent": bool(champion),
        "championApprovalMode": champion.get("approvalMode"),
        "firstPromotionRequiresManualReview": True,
        "automaticPromotionEnabled": training_status.get(
            "automaticPromotionEnabled"
        ),
    }


def main() -> int:
    payload = {
        "ok": False,
        "proofType": "MLB_ML_V3_AWS_AUDIT_EXECUTION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "snapshotsTableConfigured": bool(os.environ.get("SNAPSHOTS_TABLE")),
            "oddsApiKeyConfigured": bool(os.environ.get("ODDS_API_KEY")),
            "autoPromote": os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE"),
            "storeEnabled": os.environ.get("INQSI_MLB_ML_AUDIT_STORE", "false"),
            "allowLocalFileChampion": os.environ.get("INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION"),
        },
    }
    try:
        # Install the authority thresholds and official-lock quality classifier
        # explicitly. The workflow adds hello_world to sys.path after Python
        # startup, so relying on sitecustomize would leave the AWS audit using
        # the pre-60%-gate official classification.
        import mlb_accuracy_target_policy_v1

        policy_install = mlb_accuracy_target_policy_v1.install()

        import mlb_rolling_24h_audit
        import mlb_locked_card_audit_v1
        import mlb_ml_audit_feature_bridge_v1
        import mlb_doubleheader_safe_audit_patch

        mlb_locked_card_audit_v1.apply(mlb_rolling_24h_audit)
        mlb_ml_audit_feature_bridge_v1.apply(mlb_locked_card_audit_v1)
        mlb_doubleheader_safe_audit_patch.apply(mlb_rolling_24h_audit)

        # GitHub is a read-only audit surface. AWS owns the live experiment,
        # challenger storage, and promotion lifecycle.
        if os.environ.get("INQSI_MLB_ML_AUDIT_STORE", "false").lower() in {"1", "true", "yes"}:
            raise RuntimeError("GITHUB_MLB_AUDIT_STORAGE_FORBIDDEN_AWS_TRAINER_IS_AUTHORITATIVE")
        report = mlb_rolling_24h_audit.build(store=False, write_file=True)
        v2_training = _read_v2_training_state()
        accuracy = report.get("realWorldAccuracy") or {}
        optimization = report.get("mlOptimizationV3") or {}
        authority = report.get("mlTrainingAuthority") or {}
        critical = accuracy.get("mlCriticalFixStatus") or {}
        failures = []
        if policy_install.get("ok") is not True:
            failures.append("accuracy_target_policy_install_failed")
        if "official_lock_60pct_confirmed_direction_gate" not in (policy_install.get("patched") or []):
            failures.append("official_lock_quality_gate_not_installed")
        if accuracy.get("applied") is not True:
            failures.append("real_world_accuracy_not_applied")
        if (report.get("accuracyLedger") or {}).get("immutable") is not True:
            failures.append("immutable_accuracy_ledger_not_enabled")
        if critical.get("ok") is not True:
            failures.append("critical_ml_blocker_installation_failed")
        if optimization.get("applied") is not True:
            failures.append("ml_optimization_v3_not_applied")
        if authority.get("authoritative") != "awsNativeV2_fixed_prospective_only":
            failures.append("wrong_training_authority")
        if os.environ.get("INQSI_MLB_ML_AUTO_PROMOTE", "false").lower() in {"1", "true", "yes"}:
            failures.append("github_audit_must_not_enable_automatic_promotion")
        if authority.get("automaticChampionPromotion") is not False:
            failures.append("legacy_github_audit_automatic_promotion_not_disabled")
        if optimization.get("automaticPromotionEnabled") is not False:
            failures.append("legacy_github_optimization_write_authority_not_disabled")
        if v2_training.get("manifestPresent") and v2_training.get("manifestValid") is not True:
            failures.append("aws_v2_experiment_manifest_invalid")
        if (v2_training.get("trainingHealth") or {}).get("ok") is not True:
            failures.append("aws_v2_training_status_unhealthy_stale_or_invalid")
        if (v2_training.get("selectionCaptureHealth") or {}).get("ok") is not True:
            failures.append("aws_v2_selection_capture_status_unhealthy_stale_or_invalid")
        if v2_training.get("deploymentIdentityAgreement") is not True:
            failures.append("aws_v2_training_capture_deployment_identity_mismatch")
        if v2_training.get("ok") is not True:
            failures.append("aws_v2_mode_specific_status_unhealthy")
        if v2_training.get("automaticPromotionEnabled") is not False:
            failures.append("aws_v2_automatic_promotion_not_proven_disabled")
        if (
            v2_training.get("championPresent")
            and v2_training.get("championApprovalMode")
            not in {
                "manual_first_shadow_approval",
                "automatic_stable_champion_replacement",
                "automatic_stable_shadow_champion_replacement",
            }
        ):
            failures.append("aws_v2_champion_approval_mode_invalid")

        payload.update({
            "ok": not failures,
            "failures": failures,
            "reportCreatedAt": report.get("createdAt"),
            "reportOk": report.get("ok"),
            "summary": report.get("summary"),
            "accuracyTargetPolicyInstall": policy_install,
            "accuracyLedger": report.get("accuracyLedger"),
            "mlCriticalFixStatus": critical,
            "mlOptimizationV3": optimization,
            "mlTrainingAuthority": authority,
            "mlTrainingV2": v2_training,
            "dailyLockAuditFallback": {
                "applied": False,
                "officialAuditEligible": False,
                "policy": "Daily-card and legacy fallback rows are diagnostic-only; official audit and learning require exact canonical LOCKED#GAME authority.",
            },
            "stored": report.get("stored"),
            "storeError": report.get("storeError"),
            "githubLearningWritesDisabled": True,
            "awsNativeTrainerAuthoritative": True,
            "productionAuthoritySource": "persisted_canonical_rules_market_prediction_v2_shadow_only",
        })
    except Exception as exc:
        payload.update({
            "ok": False,
            "failures": ["audit_exception"],
            "exceptionType": type(exc).__name__,
            "exception": str(exc),
            "traceback": traceback.format_exc(),
        })

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
