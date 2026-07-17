from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v10-90pct-automatic-gated-promotion"
AUTHORITATIVE_TRAINER = "MLB-ML-OPTIMIZATION-v3-clean-dual-walk-forward-champion-challenger"
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
        applied.append("90pct_rolling_slate_and_untouched_playability_policy")
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
        "authoritativeRuntime": "MLB-ML-CHAMPION-RUNTIME-v1-shadow-until-promotion",
        "runtimeInstaller": "MLB-ML-RUNTIME-INSTALL-v3.9-explicit-verified-stage-promotion-authority",
        "runtimeInstallerCallSite": "explicit_each_lambda_entrypoint_before_route_or_writer_import",
        "singleRuntimeInstallCall": False,
        "idempotentAuthoritativeInstallerPerLambdaProcess": True,
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionSupported": True,
        "automaticPromotionEnabledOnlyByAuthoritativeAwsAudit": True,
        "automaticPromotionRequiresApplicableGate": True,
        "rolling24hAllGamesAuditTargetPct": 90.0,
        "rolling24hSlateAccuracyAuthorityTargetPct": 90.0,
        "selectedUntouchedTestAccuracyTargetPct": 90.0,
    }
