from __future__ import annotations

VERSION = "MLB-ML-CRITICAL-BLOCKERS-PATCH-v1"
_INSTALLED = False


def install() -> dict:
    global _INSTALLED
    if _INSTALLED:
        return {"ok": True, "version": VERSION, "alreadyInstalled": True}
    applied = []
    errors = []
    try:
        import mlb_audit_actionability_patch
        import mlb_ml_training_v3
        mlb_ml_training_v3.apply(mlb_audit_actionability_patch)
        applied.append("clean_dual_model_trainer")
    except Exception as exc:
        errors.append(f"trainer:{exc}")
    try:
        import mlb_ml_runtime_overlay
        import mlb_ml_runtime_safety_patch
        mlb_ml_runtime_safety_patch.apply(mlb_ml_runtime_overlay)
        applied.append("approved_champion_only_reliability_runtime")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")
    try:
        import mlb_directional_score_v1
        import mlb_directional_outcome_bridge
        mlb_directional_outcome_bridge.apply(mlb_directional_score_v1)
        applied.append("outcome_model_runtime_bridge")
    except Exception as exc:
        errors.append(f"outcome_bridge:{exc}")
    try:
        import mlb_official_prediction_semantics
        import mlb_official_freeze_bridge
        mlb_official_freeze_bridge.apply(mlb_official_prediction_semantics)
        applied.append("immutable_lock_feature_freeze")
    except Exception as exc:
        errors.append(f"freeze_bridge:{exc}")
    _INSTALLED = not errors
    return {"ok": not errors, "version": VERSION, "applied": applied, "errors": errors}
