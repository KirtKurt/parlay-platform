from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v11-aws-v2-shadow-manual-first"
AUTHORITATIVE_TRAINER = "MLB-ML-AWS-TRAINING-v1-fixed-prospective-shadow"
_INSTALLED = False


def install() -> dict:
    """Install safeguards without duplicating the runtime wrapper chain.

    Every Lambda entrypoint explicitly executes the same idempotent authoritative
    runtime installer after its prediction chain is importable.  usercustomize is
    compatibility-only. This function installs only the module-level safety and
    freeze bridges that must exist before the entrypoint's authoritative call.
    """
    global _INSTALLED
    if _INSTALLED:
        return {
            "ok": True,
            "version": VERSION,
            "alreadyInstalled": True,
            "authoritativeTrainer": AUTHORITATIVE_TRAINER,
        }

    applied = []
    errors = []

    try:
        import mlb_accuracy_target_policy_v1
        accuracy_policy = mlb_accuracy_target_policy_v1.install()
        if accuracy_policy.get("ok") is not True:
            raise RuntimeError(str(accuracy_policy.get("errors") or accuracy_policy))
        applied.append("dashboard_targets_and_v2_manual_first_policy")
    except Exception as exc:
        errors.append(f"accuracy_target_policy:{exc}")

    try:
        import mlb_ml_runtime_overlay
        import mlb_ml_runtime_safety_patch
        mlb_ml_runtime_safety_patch.apply(mlb_ml_runtime_overlay)
        applied.append("legacy_local_reliability_runtime_disabled")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")

    try:
        import mlb_official_prediction_semantics
        import mlb_official_freeze_bridge
        mlb_official_freeze_bridge.apply(mlb_official_prediction_semantics)
        applied.append("immutable_exact_lock_feature_freeze")
    except Exception as exc:
        errors.append(f"freeze_bridge:{exc}")

    _INSTALLED = not errors
    return {
        "ok": not errors,
        "version": VERSION,
        "applied": applied,
        "errors": errors,
        "authoritativeTrainer": AUTHORITATIVE_TRAINER,
        "authoritativeRuntime": "persisted_canonical_rules_market_prediction_v2_shadow_only",
        "runtimeInstaller": (
            "MLB-ML-RUNTIME-INSTALL-v4.1-verified-stage-promotion-authority-"
            "aws-v2-shadow-manual-first"
        ),
        "runtimeInstallerCallSite": "explicit_each_lambda_entrypoint_before_route_or_writer_import",
        "singleRuntimeInstallCall": False,
        "idempotentAuthoritativeInstallerPerLambdaProcess": True,
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionSupported": False,
        "automaticPromotionEnabledOnlyByAuthoritativeAwsAudit": False,
        "firstPromotionRequiresManualReview": True,
        "legacyV1AuthorityEnabled": False,
        "v2AwsNativeTraining": True,
        "rolling24hAllGamesAuditTargetPct": 90.0,
        "rolling24hAccuracyDashboardOnly": True,
        "minimumV2CleanRows": 500,
        "minimumV2ProspectiveTestRows": 100,
        "minimumV2SelectedRecommendations": 100,
    }
