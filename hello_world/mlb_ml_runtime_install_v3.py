from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-ML-RUNTIME-INSTALL-v3-single-authority-shadow-challenger"


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
        import mlb_ml_frozen_features

        for attr, patch in [
            ("_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED", mlb_fundamentals_snapshot_v1),
            ("_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED", mlb_ml_champion_runtime_v1),
            ("_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED", mlb_official_prediction_semantics),
            ("_INQSI_MLB_FROZEN_FEATURES_APPLIED", mlb_ml_frozen_features),
        ]:
            if not hasattr(engine, attr):
                patch.apply(engine)
        status["steps"]["sourceHonestFundamentals"] = hasattr(engine, "_INQSI_MLB_FUNDAMENTALS_SNAPSHOT_V1_APPLIED")
        status["steps"]["singleDdbChampionAuthority"] = hasattr(engine, "_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED")
        status["steps"]["officialSemanticsFinalized"] = hasattr(engine, "_INQSI_MLB_OFFICIAL_PREDICTION_SEMANTICS_APPLIED")
        status["steps"]["immutableFeatureFreeze"] = hasattr(engine, "_INQSI_MLB_FROZEN_FEATURES_APPLIED")
        engine.MLB_ML_RUNTIME_INSTALL_V3 = status
    except Exception as exc:
        status["steps"]["engineRuntime"] = False
        status["errors"].append(str(exc))

    status["ok"] = not status["errors"]
    status["policy"] = "The DDB champion bundle is the only model allowed to change direction or playability. Local legacy artifacts are safety-gated and otherwise shadow-only."
    return status
