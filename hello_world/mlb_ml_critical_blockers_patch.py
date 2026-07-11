from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v3-single-authority-manual-promotion"
AUTHORITATIVE_TRAINER = "MLB-ML-OPTIMIZATION-v3-clean-dual-walk-forward-champion-challenger"
_INSTALLED = False


def install() -> dict:
    """Install safeguards around the single authoritative ML optimization v3 path."""
    global _INSTALLED
    if _INSTALLED:
        return {"ok": True, "version": VERSION, "alreadyInstalled": True, "authoritativeTrainer": AUTHORITATIVE_TRAINER}
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
        applied.append("immutable_lock_feature_freeze")
    except Exception as exc:
        errors.append(f"freeze_bridge:{exc}")
    try:
        import mlb_ml_champion_challenger_v1
        import mlb_ml_manual_promotion_only_patch
        mlb_ml_manual_promotion_only_patch.apply(mlb_ml_champion_challenger_v1)
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
        "duplicateTrainerAuthorityDisabled": True,
        "duplicateOutcomeRuntimeAuthorityDisabled": True,
        "automaticPromotionDisabled": True,
        "manualPromotionWorkflow": "mlb-ml-promote-champion.yml",
    }
