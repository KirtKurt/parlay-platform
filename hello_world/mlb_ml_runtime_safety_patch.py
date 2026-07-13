from __future__ import annotations

import json
import math
import os
from typing import Any, Dict

VERSION = "MLB-ML-RUNTIME-SAFETY-v5-90pct-exact-odds-calibrated"
REQUIRED_VALIDATION_PROTOCOL = "chronological_train_validation_test_v1"
MIN_ACCURACY_TARGET_PCT = 90.0
MIN_PRODUCTION_TEST_ROWS = 100
MIN_PRODUCTION_SELECTED_TEST_ROWS = 100
MIN_EXACT_ODDS_COVERAGE_PCT = 90.0
MAX_RELIABILITY_CALIBRATION_ERROR = 0.10
MIN_ROLLING_24H_SLATE_ACCURACY_PCT = 90.0


def _accuracy_floor(value: Any = None) -> float:
    try:
        requested = float(value) if value not in {None, ""} else MIN_ACCURACY_TARGET_PCT
    except Exception:
        requested = MIN_ACCURACY_TARGET_PCT
    return max(MIN_ACCURACY_TARGET_PCT, requested)


def _optional_metric(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def apply(overlay_module: Any):
    if getattr(overlay_module, "_INQSI_MLB_RUNTIME_SAFETY_APPLIED_V3", False):
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
            and int((model.get("testMetrics") or {}).get("testCount") or 0) >= max(MIN_PRODUCTION_TEST_ROWS, int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS", "100")))
            and int((model.get("testMetrics") or {}).get("selectedCount") or 0) >= max(MIN_PRODUCTION_SELECTED_TEST_ROWS, int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS", "100")))
        )
        return model if required else None

    def strict_validated(model: Dict[str, Any] | None, info: Dict[str, Any], target: float) -> bool:
        if not model or model.get("productionApproved") is not True:
            return False
        metrics = model.get("testMetrics") or {}
        selected = int(metrics.get("selectedCount") or 0)
        accuracy = float(metrics.get("selectedAccuracyPct") or 0.0)
        price_coverage = float(metrics.get("exactOddsCoveragePct", metrics.get("priceCoveragePct")) or 0.0)
        calibration_error = _optional_metric(metrics.get("selectedCalibrationError", metrics.get("calibrationError")))
        rolling_slate_accuracy = _optional_metric(metrics.get("rolling24hSlateAccuracyPct"))
        required_accuracy = max(
            _accuracy_floor(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY", "90")),
            _accuracy_floor(model.get("promotionTargetAccuracyPct")),
            _accuracy_floor(target),
        )
        return bool(
            selected >= max(MIN_PRODUCTION_SELECTED_TEST_ROWS, int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS", "100")))
            and accuracy >= required_accuracy
            and price_coverage >= MIN_EXACT_ODDS_COVERAGE_PCT
            and calibration_error is not None
            and calibration_error <= MAX_RELIABILITY_CALIBRATION_ERROR
            and rolling_slate_accuracy is not None
            and rolling_slate_accuracy >= MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        )

    overlay_module._load_model = strict_load_model
    overlay_module._validated = strict_validated
    overlay_module.RUNTIME_SAFETY_VERSION = VERSION
    overlay_module.LOCAL_FILE_CHAMPION_DISABLED_BY_DEFAULT = True
    overlay_module.MIN_ACCURACY_TARGET_PCT = MIN_ACCURACY_TARGET_PCT
    os.environ.setdefault("INQSI_MLB_ML_MIN_GUARDED_PROMOTIONS", "0")
    os.environ["INQSI_MLB_ML_TARGET_ACCURACY"] = str(_accuracy_floor(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY", "90")))
    os.environ["INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY"] = str(_accuracy_floor(os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY", "90")))
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY"] = str(_accuracy_floor(os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY", "90")))
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS"] = str(max(MIN_PRODUCTION_TEST_ROWS, int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS", "100"))))
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS"] = str(max(MIN_PRODUCTION_SELECTED_TEST_ROWS, int(os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS", "100"))))
    overlay_module._INQSI_MLB_RUNTIME_SAFETY_APPLIED = True
    overlay_module._INQSI_MLB_RUNTIME_SAFETY_APPLIED_V2 = True
    overlay_module._INQSI_MLB_RUNTIME_SAFETY_APPLIED_V3 = True
    return overlay_module
