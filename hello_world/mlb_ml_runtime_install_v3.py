from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-ML-RUNTIME-INSTALL-v3.1-single-authority-exact-lock-vector"


def install() -> Dict[str, Any]:
    status: Dict[str, Any] = {"applied": True, "version": VERSION, "steps": {}, "errors": []}
    try:
        import mlb_ml_runtime_overlay
        import mlb_ml_runtime_safety_patch
        mlb_ml_runtime_safety_patch.apply(mlb_ml_runtime_overlay)
        status["steps"]["legacyReliabilityOverlaySafety"] = True
    except Exception as exc:
        status["steps"]["legacyReliabilityOverlaySafety"] = False
        status["errors"].append(str(exc))

    try:
        import mlb_game_winner_engine as engine
        import mlb_fundamentals_snapshot_v1
        import mlb_ml_champion_runtime_v1
        import mlb_official_prediction_semantics
        import mlb_official_freeze_bridge
        import mlb_ml_frozen_features
        import mlb_ml_exact_lock_vector_patch

        mlb_ml_exact_lock_vector_patch.apply(mlb_ml_frozen_features)
        mlb_official_freeze_bridge.apply(mlb_official_prediction_semantics)

        for attr, patch in [
            ("_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED", mlb_fundamentals_snapshot_v1),
            ("_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED", mlb_ml_champion_runtime_v1),
            ("_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED", mlb_official_prediction_semantics),
        ]:
            if not hasattr(engine, attr):
                patch.apply(engine)

        status["steps"]["sourceHonestFundamentals"] = hasattr(engine, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED")
        status["steps"]["singleDdbChampionAuthority"] = hasattr(engine, "_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED")
        status["steps"]["officialSemanticsFinalized"] = hasattr(engine, "_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED")
        status["steps"]["exactCleanCohortVectorPatch"] = getattr(mlb_ml_frozen_features, "_INQSI_MLB_EXACT_LOCK_VECTOR_PATCH_APPLIED", False)
        status["steps"]["officialFreezeBridge"] = getattr(mlb_official_prediction_semantics, "_INQSI_MLB_OFFICIAL_FREEZE_BRIDGE_APPLIED_V2", False)
        engine.MLB_ML_RUNTIME_INSTALL_V3 = status
    except Exception as exc:
        status["steps"]["engineRuntime"] = False
        status["errors"].append(str(exc))

    status["ok"] = not status["errors"] and all(status["steps"].values())
    status["policy"] = (
        "The reviewed DynamoDB champion is the only model allowed to change direction or playability. "
        "Every new locked game stores the exact immutable clean-cohort vector before final labels exist."
    )
    return status
