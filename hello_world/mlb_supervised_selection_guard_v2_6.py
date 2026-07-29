"""Executable seed-aligned regularization search for MLB V8 shadow learning.

V2.6 preserves the V2.5 provider-horizon coverage contract and every calibration,
repeatable-uplift, fold-stability, market-fallback, and promotion requirement. It
repairs optimizer comparison noise by evaluating a bounded L2 grid with the same
deterministic training shuffle for every regularization value inside a given
feature group and chronological fold.

No production, champion, cutover, or wagering authority is changed here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

try:
    import mlb_supervised_selection_guard_v2_3 as base
    import mlb_supervised_selection_guard_v2_5 as prior
except ImportError:  # package import used by unit tests
    from . import mlb_supervised_selection_guard_v2_3 as base
    from . import mlb_supervised_selection_guard_v2_5 as prior

VERSION = "MLB-SUPERVISED-SELECTION-GUARD-v2.6-seed-aligned-regularization-grid"
REGULARIZATION_GRID = (0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.50, 1.00)

BASELINE_GROUP = prior.BASELINE_GROUP
MarketBaselineModel = prior.MarketBaselineModel
IdentityCalibrator = prior.IdentityCalibrator
candidate_stability = prior.candidate_stability
feature_group_coverage = prior.feature_group_coverage


def _baseline_stability(fold_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {
        "eligible": True,
        "overallAccuracyUplift": 0.0,
        "meanDailyAccuracyUplift": 0.0,
        "netCorrectUplift": 0,
        "minimumNetCorrectUplift": 0,
        "foldAccuracyUplifts": [0.0 for _ in fold_rows],
        "positiveFoldCount": 0,
        "requiredPositiveFoldCount": 0,
        "worstFoldAccuracyUplift": 0.0,
        "errors": [],
    }


def _decorate_selection_guard(result: Dict[str, Any]) -> Dict[str, Any]:
    selection_guard = result.get("selectionGuard") or {}
    selection_guard["version"] = VERSION
    thresholds = selection_guard.get("thresholds") or {}
    thresholds.update(
        {
            "minimumBbsSupportedOverallGames": prior.MIN_BBS_SUPPORTED_OVERALL_GAMES,
            "minimumBbsSupportedTrainingFoldGames": prior.MIN_BBS_SUPPORTED_TRAIN_GAMES,
            "minimumBbsSupportedValidationFoldGames": prior.MIN_BBS_SUPPORTED_VALIDATION_GAMES,
            "minimumBbsEvaluableTrainingFolds": prior.MIN_BBS_EVALUABLE_TRAIN_FOLDS,
            "minimumBbsEvaluableValidationFolds": prior.MIN_BBS_EVALUABLE_VALIDATION_FOLDS,
            "bbsCoverageDenominator": prior.BBS_SUPPORT_FEATURE,
            "providerHorizonUnsupportedFoldsFailCoverage": False,
            "providerHorizonUnsupportedFoldsCountAsPassing": False,
            "v8FullGameRequiresFirstFive": False,
            "regularizationGrid": list(REGULARIZATION_GRID),
            "regularizationComparisonSeedAligned": True,
            "regularizationGridBounded": True,
        }
    )
    selection_guard["thresholds"] = thresholds
    result["selectionGuard"] = selection_guard
    return result


def _trainer_contract_available(model_module: Any) -> bool:
    feature_module = getattr(model_module, "features", None)
    return bool(
        feature_module is not None
        and callable(getattr(model_module, "inner_expanding_folds", None))
        and callable(getattr(model_module, "_subset", None))
        and callable(getattr(model_module, "fit_residual_logistic", None))
        and callable(getattr(model_module, "evaluate_probabilities", None))
        and callable(getattr(model_module, "_market_metrics", None))
    )


def _install_executable_selector(model_module: Any) -> None:
    feature_module = model_module.features

    def nested_select(
        examples: Sequence[Any], train_days: Sequence[str], *, seed: int = 260726
    ) -> Dict[str, Any]:
        folds = model_module.inner_expanding_folds(train_days)
        l2_values = tuple(REGULARIZATION_GRID)
        development = model_module._subset(examples, train_days)
        training_partitions = [
            model_module._subset(examples, inner_train_days)
            for inner_train_days, _ in folds
        ]
        validation_partitions = [
            model_module._subset(examples, validation_days)
            for _, validation_days in folds
        ]
        candidates: List[Dict[str, Any]] = []

        for group_index, (group, feature_names) in enumerate(
            feature_module.FEATURE_GROUPS.items()
        ):
            group_coverage = feature_group_coverage(
                development,
                group=group,
                feature_names=feature_names,
                training_partitions=training_partitions,
                validation_partitions=validation_partitions,
            )
            for l2 in l2_values:
                fold_rows: List[Dict[str, Any]] = []
                all_probabilities: List[float] = []
                all_outcomes: List[int] = []
                all_examples: List[Any] = []

                for fold_index, (inner_train_days, validation_days) in enumerate(folds):
                    inner_train = model_module._subset(examples, inner_train_days)
                    validation = model_module._subset(examples, validation_days)
                    fitted = model_module.fit_residual_logistic(
                        inner_train,
                        feature_group=group,
                        l2=l2,
                        seed=seed + group_index * 1000 + fold_index,
                        steps=220,
                    )
                    probabilities = [fitted.raw_probability(row) for row in validation]
                    metrics = model_module.evaluate_probabilities(validation, probabilities)
                    market = model_module._market_metrics(validation)
                    fold_rows.append(
                        {
                            "fold": fold_index + 1,
                            "trainFirstDate": min(inner_train_days),
                            "trainLastDate": max(inner_train_days),
                            "validationFirstDate": min(validation_days),
                            "validationLastDate": max(validation_days),
                            "metrics": metrics,
                            "marketBaseline": market,
                        }
                    )
                    all_probabilities.extend(probabilities)
                    all_outcomes.extend(row.outcome for row in validation)
                    all_examples.extend(validation)

                aggregate = model_module.evaluate_probabilities(
                    all_examples, all_probabilities
                )
                market_aggregate = model_module._market_metrics(all_examples)
                if group == BASELINE_GROUP:
                    stability = _baseline_stability(fold_rows)
                else:
                    stability = candidate_stability(
                        aggregate,
                        market_aggregate,
                        fold_rows,
                        calibration_eligible=getattr(
                            model_module,
                            "_INQSI_MLB_CALIBRATION_ELIGIBLE",
                            lambda candidate_metrics, market_metrics: True,
                        ),
                    )
                errors = list(group_coverage.get("errors") or []) + list(
                    stability.get("errors") or []
                )
                candidate = {
                    "featureGroup": group,
                    "featureCount": len(tuple(feature_names)),
                    "l2": l2,
                    "folds": fold_rows,
                    "oofMetrics": aggregate,
                    "oofMarketBaseline": market_aggregate,
                    "oofProbabilities": all_probabilities,
                    "oofOutcomes": all_outcomes,
                    "guard": {
                        "eligible": group == BASELINE_GROUP or not errors,
                        "coverage": group_coverage,
                        "stability": stability,
                        "errors": errors,
                    },
                }
                candidate["selectionKey"] = base._selection_key(candidate)
                candidates.append(candidate)

        selected = min(candidates, key=lambda row: tuple(row["selectionKey"]))
        model_module._INQSI_MLB_IDENTITY_CALIBRATOR_ONCE = (
            selected["featureGroup"] == BASELINE_GROUP
        )
        ablations: Dict[str, Any] = {}
        for group in feature_module.FEATURE_GROUPS:
            row = min(
                (item for item in candidates if item["featureGroup"] == group),
                key=lambda item: tuple(item["selectionKey"]),
            )
            ablations[group] = {
                "l2": row["l2"],
                "oofMetrics": row["oofMetrics"],
                "oofMarketBaseline": row["oofMarketBaseline"],
                "folds": row["folds"],
                "guard": row["guard"],
            }

        result = {
            "selectedFeatureGroup": selected["featureGroup"],
            "selectedL2": selected["l2"],
            "selectedOofMetrics": selected["oofMetrics"],
            "selectedOofMarketBaseline": selected["oofMarketBaseline"],
            "selectedOofProbabilities": selected["oofProbabilities"],
            "selectedOofOutcomes": selected["oofOutcomes"],
            "ablation": ablations,
            "candidateCount": len(candidates),
            "foldCount": len(folds),
            "selectionUsedUntouchedAudit": False,
            "selectionGuard": {
                "version": VERSION,
                "baselineFallbackUsed": selected["featureGroup"] == BASELINE_GROUP,
                "selectedCandidateEligible": bool(selected["guard"]["eligible"]),
                "selectedCandidateErrors": list(selected["guard"]["errors"]),
                "thresholds": {
                    "minimumOofAccuracyUplift": base.MIN_OOF_ACCURACY_UPLIFT,
                    "minimumOofNetCorrect": base.MIN_OOF_NET_CORRECT,
                    "maximumWorstFoldAccuracyRegression": base.MAX_WORST_FOLD_ACCURACY_REGRESSION,
                    "minimumPositiveFoldRatio": base.MIN_POSITIVE_FOLD_RATIO,
                    "minimumFundamentalsCoverage": base.MIN_FUNDAMENTALS_COVERAGE,
                    "minimumFundamentalsMetricCoverage": base.MIN_FUNDAMENTALS_METRIC_COVERAGE,
                    "minimumBbsPriorCoverage": base.MIN_BBS_PRIOR_COVERAGE,
                    "minimumV8MarketCoverage": base.MIN_V8_MARKET_COVERAGE,
                    "minimumV8FirstFiveCoverage": base.MIN_V8_FIRST_FIVE_COVERAGE,
                },
                "selectedCoverage": selected["guard"]["coverage"],
                "selectedStability": selected["guard"]["stability"],
                "learnedEligibleCandidateCount": sum(
                    item["featureGroup"] != BASELINE_GROUP
                    and bool(item["guard"]["eligible"])
                    for item in candidates
                ),
            },
        }
        return _decorate_selection_guard(result)

    model_module.nested_select = nested_select


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_SELECTION_GUARD_V2_6_INSTALLED", False):
        return model_module

    # Install the proven V2.5 coverage, fit, calibration, and train wrappers first.
    base.VERSION = VERSION
    base.REGULARIZATION_GRID = tuple(REGULARIZATION_GRID)
    prior.VERSION = VERSION
    prior.install(model_module)

    if _trainer_contract_available(model_module):
        _install_executable_selector(model_module)
    else:
        nested = getattr(model_module, "nested_select", None)
        if callable(nested):
            original_nested = nested

            def nested_with_v26_metadata(*args: Any, **kwargs: Any) -> Dict[str, Any]:
                return _decorate_selection_guard(original_nested(*args, **kwargs))

            model_module.nested_select = nested_with_v26_metadata

    model_module._INQSI_MLB_SELECTION_GUARD_V2_6_METADATA_INSTALLED = True
    model_module._INQSI_MLB_SELECTION_GUARD_V2_6_INSTALLED = True
    return model_module
