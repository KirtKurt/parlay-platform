from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v9-separated-accuracy-targets"
AUTHORITATIVE_TRAINER = "MLB-ML-OPTIMIZATION-v3-clean-dual-walk-forward-champion-challenger"
_INSTALLED = False


def install() -> dict:
    """Install safeguards without duplicating the runtime wrapper chain.

    `usercustomize.py` executes the one authoritative runtime installer after the
    legacy prediction chain has been assembled. This function only installs
    module-level safety and freeze bridges that must exist before that final call.
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
        applied.append("90pct_all_games_audit_60pct_recommendation_policy")
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

    try:
        import mlb_ml_champion_challenger_v1
        import mlb_ml_manual_promotion_only_v1
        mlb_ml_manual_promotion_only_v1.apply(mlb_ml_champion_challenger_v1)
        applied.append("automatic_champion_promotion_permanently_disabled")
    except Exception as exc:
        errors.append(f"promotion_safety:{exc}")

    _INSTALLED = not errors
    return {
        "ok": not errors,
        "version": VERSION,
        "applied": applied,
        "errors": errors,
        "authoritativeTrainer": AUTHORITATIVE_TRAINER,
        "authoritativeRuntime": "MLB-ML-CHAMPION-RUNTIME-v1-shadow-until-promotion",
        "runtimeInstaller": "MLB-ML-RUNTIME-INSTALL-v3.4-canonical-exact-vector-storage",
        "runtimeInstallerCallSite": "usercustomize.py_after_prediction_chain",
        "singleRuntimeInstallCall": True,
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionDisabled": True,
        "rolling24hAllGamesAuditTargetPct": 90.0,
        "recommendationReliabilityThresholdPct": 60.0,
        "manualPromotionModule": "MLB-ML-MANUAL-PROMOTION-ONLY-v1",
        "manualPromotionWorkflow": "mlb-ml-promote-champion.yml",
    }
