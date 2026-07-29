"""Stable daily-slate objective for the supervised MLB V8 shadow model.

V2.6 keeps every V2.5 fail-closed calibration, coverage, repeatable-uplift,
provider-horizon, and market-fallback rule while executing a bounded L2 search
with identical deterministic shuffles inside each feature-group and chronological-
fold comparison. It never changes production authority.

The target-game context overlay is installed inside the existing prior-game overlay
so both independently validated manifest families are composed before features are
compiled. No selection or promotion threshold is changed.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

try:
    import mlb_supervised_feature_interactions_v2_2 as feature_interactions
    import mlb_supervised_feature_boundaries_v2_4 as feature_boundaries
    import mlb_supervised_feature_groups_v2_4 as feature_groups
    import mlb_supervised_selection_guard_v2_6 as selection_guard
    import mlb_v8_historical_bbs_overlay_v1 as historical_bbs_overlay
    import mlb_v8_historical_bbs_prior_game_features_v1 as historical_bbs_prior_features
    import mlb_v8_historical_context_overlay_v1 as historical_context_overlay
except ImportError:  # package import used by unit tests
    from . import mlb_supervised_feature_interactions_v2_2 as feature_interactions
    from . import mlb_supervised_feature_boundaries_v2_4 as feature_boundaries
    from . import mlb_supervised_feature_groups_v2_4 as feature_groups
    from . import mlb_supervised_selection_guard_v2_6 as selection_guard
    from . import mlb_v8_historical_bbs_overlay_v1 as historical_bbs_overlay
    from . import mlb_v8_historical_bbs_prior_game_features_v1 as historical_bbs_prior_features
    from . import mlb_v8_historical_context_overlay_v1 as historical_context_overlay

VERSION = "MLB-SUPERVISED-SHADOW-v2.6-seed-aligned-regularization-grid"
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
    if getattr(model_module, "_INQSI_MLB_DAILY_OBJECTIVE_V2_6_INSTALLED", False):
        return model_module
    feature_module = getattr(model_module, "features", None)
    if feature_module is not None:
        feature_interactions.install(feature_module)
        feature_boundaries.install_features(feature_module)
        historical_bbs_prior_features.install(feature_module)
        feature_groups.install(feature_module)
    # Install the target overlay first. The existing prior-game wrapper is then the
    # outer wrapper, so target context sees and composes the prior snapshot instead
    # of either family overwriting the other.
    if callable(getattr(model_module, "train_and_evaluate", None)):
        historical_context_overlay.install(model_module)
        historical_bbs_overlay.install(model_module)
    model_module._config_key = daily_objective_key
    model_module._INQSI_MLB_CALIBRATION_ELIGIBLE = calibration_eligible
    selection_guard.install(model_module)
    if callable(getattr(model_module, "train_and_evaluate", None)):
        feature_boundaries.install_model(model_module)
    model_module.VERSION = VERSION
    model_module.SUPERVISED_SELECTION_OBJECTIVE = {
        "version": VERSION,
        "primary": [
            "stableMarketRelativeOverallAccuracyUplift",
            "stableMarketRelativeMeanDailyAccuracyUplift",
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
        "selectionGuardVersion": selection_guard.VERSION,
        "coverageEligibilityRequired": True,
        "coverageDenominatorVersion": selection_guard.VERSION,
        "bbsPriorSupportedCohortStartDate": historical_bbs_prior_features.BBS_PRIOR_SUPPORT_START_DATE,
        "bbsProviderHorizonFoldPolicy": "require_two_evaluable_training_and_validation_folds",
        "bbsUnsupportedFoldsCountAsPassing": False,
        "regularizationGrid": list(selection_guard.REGULARIZATION_GRID),
        "regularizationComparisonSeedAligned": True,
        "regularizationGridBounded": True,
        "v8FullGameCandidateRequiresFirstFive": False,
        "featureGroupsVersion": feature_groups.VERSION,
        "featureBoundariesVersion": feature_boundaries.VERSION,
        "targetGameFundamentalsExcludeBbsPriorGameSnapshots": True,
        "targetGameContextComposedAfterPriorGameOverlay": True,
        "targetGameContextRequiresStarterBullpenLineupInjuryParkWeather": True,
        "targetGameContextTargetOutcomeUsed": False,
        "targetGameContextSameDayResultsExcluded": True,
        "foldStabilityRequired": True,
        "marketBaselineFallbackEnabled": True,
        "featureInteractionVersion": feature_interactions.VERSION,
        "historicalBbsOverlayVersion": historical_bbs_overlay.VERSION,
        "historicalTargetContextOverlayVersion": historical_context_overlay.VERSION,
        "historicalBbsPriorGameFeatureVersion": historical_bbs_prior_features.VERSION,
        "historicalBbsPointInTimeRequired": True,
        "historicalBbsSelectionUsesOutcomes": False,
        "historicalBbsTargetOutcomeUsed": False,
        "historicalBbsSameDayResultsExcluded": True,
        "untouchedAuditUsedForSelection": False,
        "productionAuthorityChanged": False,
    }
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_3_INSTALLED = True
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_4_INSTALLED = True
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_5_INSTALLED = True
    model_module._INQSI_MLB_DAILY_OBJECTIVE_V2_6_INSTALLED = True
    return model_module
