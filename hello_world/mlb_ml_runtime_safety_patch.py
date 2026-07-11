from __future__ import annotations

import json
import os
from typing import Any, Dict

VERSION = "MLB-ML-RUNTIME-SAFETY-v2-ddb-champion-single-authority"
REQUIRED_VALIDATION_PROTOCOL = "chronological_train_validation_test_v1"


def apply(overlay_module: Any):
    if getattr(overlay_module, "_INQSI_MLB_RUNTIME_SAFETY_APPLIED_V2", False):
        return overlay_module

    def strict_load_model() -> Dict[str, Any] | None:
        # The DDB champion runtime is the production authority. Local-file models are
        # disabled unless an explicit emergency/rollback flag is enabled.
        allow_local = os.environ.get("INQSI_MLB_ALLOW_LOCAL_FILE_CHAMPION", "false").lower() in {"1", "true", "yes"}
        if not allow_local:
            return None
        path = os.environ.get("INQSI_MLB_ML_MODEL_PATH", getattr(overlay_module, "MODEL_PATH", "runtime_reports/mlb_ml_model_latest.json"))
        try:
            with open(path, encoding="utf-8") as fh:
                model = json.load(fh)
        except Exception:
            return None
        if not isinstance(model, dict) or model.get("ok") is not True:
            return None
        required = bool(
            model.get("productionApproved") is True
            and model.get("cleanCohort") is True
            and model.get("modelRole") == "reliability"
            and model.get("validationProtocol") == REQUIRED_VALIDATION_PROTOCOL
            and model.get("featureFreezeRequired") is True
            and int((model.get("testMetrics") or {}).get("testCount") or 0) >= int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS", "100"))
            and int((model.get("testMetrics") or {}).get("selectedCount") or 0) >= int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS", "50"))
        )
        return model if required else None

    def strict_validated(model: Dict[str, Any] | None, info: Dict[str, Any], target: float) -> bool:
        if not model or model.get("productionApproved") is not True:
            return False
        metrics = model.get("testMetrics") or {}
        selected = int(metrics.get("selectedCount") or 0)
        accuracy = float(metrics.get("selectedAccuracyPct") or 0.0)
        roi = metrics.get("selectedFlatUnitRoiPct")
        price_coverage = float(metrics.get("priceCoveragePct") or 0.0)
        required_accuracy = float(model.get("promotionTargetAccuracyPct") or 60.0)
        return bool(
            selected >= int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS", "50"))
            and accuracy >= required_accuracy
            and price_coverage >= 90.0
            and roi is not None and float(roi) > 0.0
        )

    overlay_module._load_model = strict_load_model
    overlay_module._validated = strict_validated
    overlay_module.RUNTIME_SAFETY_VERSION = VERSION
    overlay_module.LOCAL_FILE_CHAMPION_DISABLED_BY_DEFAULT = True
    os.environ.setdefault("INQSI_MLB_ML_MIN_GUARDED_PROMOTIONS", "0")
    os.environ.setdefault("INQSI_MLB_ML_TARGET_ACCURACY", "60")
    overlay_module._INQSI_MLB_RUNTIME_SAFETY_APPLIED = True
    overlay_module._INQSI_MLB_RUNTIME_SAFETY_APPLIED_V2 = True
    return overlay_module
