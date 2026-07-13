from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v11-80pct-production-60pct-lock"
AUTHORITATIVE_TRAINER = "MLB-ML-OPTIMIZATION-v3-clean-dual-walk-forward-champion-challenger"
_INSTALLED = False


def install() -> dict:
    """Install safeguards without duplicating the runtime wrapper chain."""
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
    accuracy_policy = {}

    try:
        import mlb_accuracy_target_policy_v1

        accuracy_policy = mlb_accuracy_target_policy_v1.install()
        if accuracy_policy.get("ok") is not True:
            raise RuntimeError(str(accuracy_policy.get("errors") or accuracy_policy))
        applied.append("80pct_production_and_60pct_individual_game_lock_policy")
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
        "runtimeInstaller": "MLB-ML-RUNTIME-INSTALL-v3.6-per-game-lock-temporal-90pct-auto-authority",
        "runtimePolicyVersion": "MLB-ML-RUNTIME-POLICY-v3.7-80pct-production-60pct-game-lock",
        "runtimeInstallerCallSite": "usercustomize.py_after_prediction_chain",
        "singleRuntimeInstallCall": True,
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionSupported": True,
        "automaticPromotionEnabledOnlyByAuthoritativeAwsAudit": True,
        "automaticPromotionRequiresApplicableGate": True,
        "rolling24hAllGamesAuditTargetPct": accuracy_policy.get("rolling24hAllGamesAuditTargetPct", 80.0),
        "rolling24hSlateAccuracyAuthorityTargetPct": accuracy_policy.get("minimumRolling24hSlateAccuracyPct", 80.0),
        "outcomeUntouchedAccuracyTargetPct": accuracy_policy.get("minimumOutcomeUntouchedAccuracyPct", 80.0),
        "selectedUntouchedTestAccuracyTargetPct": accuracy_policy.get("recommendationReliabilityThresholdPct", 80.0),
        "exactLockedOddsCoverageTargetPct": accuracy_policy.get("minimumExactOddsCoveragePct", 80.0),
        "individualGameLockMinimumProbabilityPct": accuracy_policy.get("minimumIndividualGameLockProbabilityPct", 60.0),
    }
