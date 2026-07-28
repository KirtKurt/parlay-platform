"""Stable daily-slate objective for the supervised MLB shadow model.

V2.2 keeps calibration fail-closed, rewards repeatable market-relative accuracy
uplift, and installs leakage-safe nonlinear regime interactions without changing
production authority or touching the untouched audit during selection.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

try:
    import mlb_supervised_feature_interactions_v2_2 as feature_interactions
except ImportError:  # package import used by unit tests
    from . import mlb_supervised_feature_interactions_v2_2 as feature_interactions

VERSION = "MLB-SUPERVISED-SHADOW-v2.2-regime-interactions-market-uplift-calibration-safe"
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
    """Return a lower-is-better key aligned to repeatable predictive edge."""
    eligible_penalty = 0.0 if calibration_eligible(metrics, market) else 1.0
    overall_uplift = _f(metrics.get("overallAccuracy"), 0.0) - _f(
        market.get("overallAccuracy"), 0.0
    )
    mean_daily_uplift = _f(metrics.get("meanDailyAccuracy"), 0.0) - _f(
        market.get("meanDailyAccuracy"), 0.0
    )
    pass_rate_uplift = _f(metrics.get("dailyPassRate"), 0.0) - _f(
        market.get("dailyPassRate"), 0.0
    )
    return (
        eligible_penalty,
        -overall_uplift,
        -mean_daily_uplift,
        -pass_rate_uplift,
        -_f(metrics.get("overallAccuracy"), 0.0),
        -_f(metrics.get("meanDailyAccuracy"), 0.0),
        -_f(metrics.get("minimumDailyAccuracy"), 0.0),
        _f(metrics.get("logLoss"), 10.0),
        _f(metrics.get("brierScore"), 1.0),
        _f(metrics.get("expectedCalibrationError"), 1.0),
    )


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_DAILY_OBJECTIVE_V2_2_INSTALLED", False):
        return model_module
    feature_module = getattr(model_module, "features", None)
    if feature_module is not None:
        feature_interactions.install(feature_module)
    model_module._config_key = daily_objective_key
    model_module.VERSION = VERSION
    model_module.SUPERVISED_SELECTION_OBJECTIVE = {
        "version": VERSION,
        "primary": [
            "marketRelativeOverallAccuracyUplift",
            "marketRelativeMeanDailyAccuracyUplift",
            "marketRelativeDailyPassRateUplift",
            "overallAccuracy",
            "meanDailyAccuracy",
            "minimumDailyAccuracy",
        ],
        "calibrationEligibility": {
            "maximumBrierDegradation": MAX_BRIER_DEGRADATION,
            "maximumLogLossDegradation": MAX_LOG_LOSS_DEGRADATION,
            "maximumExpectedCalibrationError": MAX_ECE,
        },
        "featureInteractionVersion": feature_interactions.VERSION,
        "untouchedAuditUsedForSelection": False,
        "productionAuthorityChanged": False,
    }
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_2_INSTALLED = True
    return model_module
