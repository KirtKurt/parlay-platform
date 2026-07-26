"""Daily-slate selection objective for the supervised MLB shadow model.

The prior nested selector optimized log loss first even though promotion requires
80% accuracy on every complete slate. This patch keeps calibration fail-closed,
then ranks eligible candidates by daily pass rate, minimum daily accuracy, mean
daily accuracy, and overall accuracy before calibration tie-breakers.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

VERSION = "MLB-SUPERVISED-SHADOW-v2.1-daily-slate-objective-calibration-safe"
MAX_BRIER_DEGRADATION = 0.005
MAX_LOG_LOSS_DEGRADATION = 0.010
MAX_ECE = 0.080


def _f(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def calibration_eligible(metrics: Mapping[str, Any], market: Mapping[str, Any]) -> bool:
    return bool(
        _f(metrics.get("brierScore"), 1.0)
        <= _f(market.get("brierScore"), 1.0) + MAX_BRIER_DEGRADATION
        and _f(metrics.get("logLoss"), 10.0)
        <= _f(market.get("logLoss"), 10.0) + MAX_LOG_LOSS_DEGRADATION
        and _f(metrics.get("expectedCalibrationError"), 1.0) <= MAX_ECE
    )


def daily_objective_key(
    metrics: Mapping[str, Any], market: Mapping[str, Any]
) -> Tuple[float, ...]:
    """Return a lower-is-better key aligned to the actual promotion contract."""
    eligible_penalty = 0.0 if calibration_eligible(metrics, market) else 1.0
    return (
        eligible_penalty,
        -_f(metrics.get("dailyPassRate"), 0.0),
        -_f(metrics.get("minimumDailyAccuracy"), 0.0),
        -_f(metrics.get("meanDailyAccuracy"), 0.0),
        -_f(metrics.get("overallAccuracy"), 0.0),
        _f(metrics.get("logLoss"), 10.0),
        _f(metrics.get("brierScore"), 1.0),
        _f(metrics.get("expectedCalibrationError"), 1.0),
    )


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_DAILY_OBJECTIVE_V2_1_INSTALLED", False):
        return model_module
    model_module._config_key = daily_objective_key
    model_module.VERSION = VERSION
    model_module.SUPERVISED_SELECTION_OBJECTIVE = {
        "version": VERSION,
        "primary": [
            "dailyPassRate",
            "minimumDailyAccuracy",
            "meanDailyAccuracy",
            "overallAccuracy",
        ],
        "calibrationEligibility": {
            "maximumBrierDegradation": MAX_BRIER_DEGRADATION,
            "maximumLogLossDegradation": MAX_LOG_LOSS_DEGRADATION,
            "maximumExpectedCalibrationError": MAX_ECE,
        },
        "untouchedAuditUsedForSelection": False,
        "productionAuthorityChanged": False,
    }
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_1_INSTALLED = True
    return model_module
