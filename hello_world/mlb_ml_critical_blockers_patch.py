from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v7-runtime-installer-executed"
AUTHORITATIVE_TRAINER = "MLB-ML-OPTIMIZATION-v3-clean-dual-walk-forward-champion-challenger"
_INSTALLED = False
_INSTALL_RESULT = None


def install() -> dict:
    """Install safeguards and execute the single authoritative MLB ML runtime."""
    global _INSTALLED, _INSTALL_RESULT
    if _INSTALLED:
        return {
            "ok": True,
            "version": VERSION,
            "alreadyInstalled": True,
            "authoritativeTrainer": AUTHORITATIVE_TRAINER,
            "runtimeInstallation": _INSTALL_RESULT,
        }

    applied = []
    errors = []

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

    try:
        import mlb_ml_runtime_install_v3
        _INSTALL_RESULT = mlb_ml_runtime_install_v3.install()
        if _INSTALL_RESULT.get("ok") is not True:
            errors.append(f"runtime_install:{_INSTALL_RESULT}")
        else:
            applied.append("single_authority_runtime_installed")
    except Exception as exc:
        _INSTALL_RESULT = {"ok": False, "error": str(exc)}
        errors.append(f"runtime_install:{exc}")

    _INSTALLED = not errors
    return {
        "ok": not errors,
        "version": VERSION,
        "applied": applied,
        "errors": errors,
        "authoritativeTrainer": AUTHORITATIVE_TRAINER,
        "authoritativeRuntime": "MLB-ML-CHAMPION-RUNTIME-v1-shadow-until-promotion",
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionDisabled": True,
        "manualPromotionModule": "MLB-ML-MANUAL-PROMOTION-ONLY-v1",
        "manualPromotionWorkflow": "mlb-ml-promote-champion.yml",
        "runtimeInstallation": _INSTALL_RESULT,
    }
