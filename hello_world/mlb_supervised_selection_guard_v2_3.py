"""Coverage- and stability-aware selection guard for the MLB V8 shadow model.

This module prevents sparse optional feature groups or one-fold wins from displacing
an already strong market prior. Selection uses development folds only. When no
learned residual demonstrates repeatable out-of-fold uplift, the challenger falls
back to the unmodified market probability and remains shadow-only.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "MLB-SUPERVISED-SELECTION-GUARD-v2.3"
BASELINE_GROUP = "market_baseline"
MIN_OOF_ACCURACY_UPLIFT = 0.005
MIN_OOF_NET_CORRECT = 3
MAX_WORST_FOLD_ACCURACY_REGRESSION = 0.01
MIN_POSITIVE_FOLD_RATIO = 2.0 / 3.0
MIN_FUNDAMENTALS_COVERAGE = 0.50
MIN_FUNDAMENTALS_METRIC_COVERAGE = 0.50
MIN_BBS_PRIOR_COVERAGE = 0.50
MIN_V8_MARKET_COVERAGE = 0.60
MIN_V8_FIRST_FIVE_COVERAGE = 0.40


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _coverage(examples: Sequence[Any], feature_name: str) -> float:
    if not examples:
        return 0.0
    values = [_f(getattr(row, "features", {}).get(feature_name)) for row in examples]
    if feature_name == "fundamentals_group_coverage":
        return sum(max(0.0, min(1.0, value)) for value in values) / len(values)
    return sum(value > 0.5 for value in values) / len(values)


def _coverage_requirements(group: str, feature_names: Iterable[str]) -> Dict[str, float]:
    names = set(feature_names)
    requirements: Dict[str, float] = {}
    if "fundamentals" in group:
        requirements["fundamentals_available"] = MIN_FUNDAMENTALS_COVERAGE
        if "fundamentals_group_coverage" in names:
            requirements["fundamentals_group_coverage"] = MIN_FUNDAMENTALS_METRIC_COVERAGE
        if "bbs_prior_available" in names:
            requirements["bbs_prior_available"] = MIN_BBS_PRIOR_COVERAGE
    if "_v8" in group:
        requirements["v8_available"] = MIN_V8_MARKET_COVERAGE
        requirements["v8_f5_available"] = MIN_V8_FIRST_FIVE_COVERAGE
    return requirements


def feature_group_coverage(
    examples: Sequence[Any],
    *,
    group: str,
    feature_names: Iterable[str],
    training_partitions: Sequence[Sequence[Any]] = (),
    validation_partitions: Sequence[Sequence[Any]] = (),
) -> Dict[str, Any]:
    requirements = _coverage_requirements(group, feature_names)
    overall = {name: round(_coverage(examples, name), 8) for name in requirements}
    training = [
        {name: round(_coverage(rows, name), 8) for name in requirements}
        for rows in training_partitions
    ]
    validation = [
        {name: round(_coverage(rows, name), 8) for name in requirements}
        for rows in validation_partitions
    ]
    errors: List[str] = []
    for name, minimum in requirements.items():
        if overall.get(name, 0.0) + 1e-12 < minimum:
            errors.append(f"overall_{name}_below_{minimum:.2f}")
        for index, row in enumerate(training, start=1):
            if row.get(name, 0.0) + 1e-12 < minimum:
                errors.append(f"train_fold_{index}_{name}_below_{minimum:.2f}")
        for index, row in enumerate(validation, start=1):
            if row.get(name, 0.0) + 1e-12 < minimum:
                errors.append(f"validation_fold_{index}_{name}_below_{minimum:.2f}")
    return {
        "eligible": not errors,
        "requirements": requirements,
        "overall": overall,
        "trainingFolds": training,
        "validationFolds": validation,
        "errors": errors,
    }


def candidate_stability(
    metrics: Mapping[str, Any],
    market: Mapping[str, Any],
    folds: Sequence[Mapping[str, Any]],
    *,
    calibration_eligible: Any,
) -> Dict[str, Any]:
    game_count = int(metrics.get("gameCount") or 0)
    overall_uplift = _f(metrics.get("overallAccuracy")) - _f(market.get("overallAccuracy"))
    mean_daily_uplift = _f(metrics.get("meanDailyAccuracy")) - _f(market.get("meanDailyAccuracy"))
    net_correct = int(metrics.get("correct") or 0) - int(market.get("correct") or 0)
    minimum_net_correct = max(MIN_OOF_NET_CORRECT, int(math.ceil(game_count * MIN_OOF_ACCURACY_UPLIFT)))
    fold_uplifts: List[float] = []
    for fold in folds:
        fold_metrics = fold.get("metrics") or {}
        fold_market = fold.get("marketBaseline") or {}
        fold_uplifts.append(
            _f(fold_metrics.get("overallAccuracy")) - _f(fold_market.get("overallAccuracy"))
        )
    positive_fold_count = sum(value > 1e-12 for value in fold_uplifts)
    required_positive_folds = max(1, int(math.ceil(len(fold_uplifts) * MIN_POSITIVE_FOLD_RATIO)))
    worst_fold_uplift = min(fold_uplifts) if fold_uplifts else -1.0
    errors: List[str] = []
    if not calibration_eligible(metrics, market):
        errors.append("aggregate_calibration_ineligible")
    if overall_uplift + 1e-12 < MIN_OOF_ACCURACY_UPLIFT:
        errors.append("aggregate_accuracy_uplift_below_floor")
    if net_correct < minimum_net_correct:
        errors.append("net_correct_uplift_below_floor")
    if mean_daily_uplift < -1e-12:
        errors.append("mean_daily_accuracy_worse_than_market")
    if positive_fold_count < required_positive_folds:
        errors.append("positive_fold_count_below_floor")
    if worst_fold_uplift < -MAX_WORST_FOLD_ACCURACY_REGRESSION - 1e-12:
        errors.append("worst_fold_accuracy_regression_too_large")
    return {
        "eligible": not errors,
        "overallAccuracyUplift": round(overall_uplift, 8),
        "meanDailyAccuracyUplift": round(mean_daily_uplift, 8),
        "netCorrectUplift": net_correct,
        "minimumNetCorrectUplift": minimum_net_correct,
        "foldAccuracyUplifts": [round(value, 8) for value in fold_uplifts],
        "positiveFoldCount": positive_fold_count,
        "requiredPositiveFoldCount": required_positive_folds,
        "worstFoldAccuracyUplift": round(worst_fold_uplift, 8),
        "errors": errors,
    }


def _selection_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    guard = candidate.get("guard") or {}
    stability = guard.get("stability") or {}
    metrics = candidate.get("oofMetrics") or {}
    feature_count = int(candidate.get("featureCount") or 0)
    eligible = bool(guard.get("eligible"))
    return (
        0.0 if eligible else 1.0,
        -_f(stability.get("worstFoldAccuracyUplift"), -1.0),
        -_f(stability.get("positiveFoldCount")),
        -_f(stability.get("overallAccuracyUplift")),
        -_f(stability.get("netCorrectUplift")),
        -_f(stability.get("meanDailyAccuracyUplift")),
        float(feature_count),
        _f(metrics.get("logLoss"), 10.0),
        _f(metrics.get("brierScore"), 1.0),
        _f(metrics.get("expectedCalibrationError"), 1.0),
        str(candidate.get("featureGroup") or ""),
        _f(candidate.get("l2"), 0.0),
    )


class MarketBaselineModel:
    def __init__(self, model_module: Any, *, seed: int):
        self._model_module = model_module
        self.feature_group = BASELINE_GROUP
        self.seed = int(seed)

    def raw_probability(self, row: Any) -> float:
        return max(1e-9, min(1.0 - 1e-9, float(row.market_probability)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": getattr(self._model_module, "VERSION", VERSION),
            "featureGroup": BASELINE_GROUP,
            "standardizer": {"featureNames": [], "means": [], "scales": []},
            "weights": [],
            "intercept": 0.0,
            "l2": 0.0,
            "trainingSteps": 0,
            "seed": self.seed,
            "fallback": "unmodified_market_probability",
        }


class IdentityCalibrator:
    slope = 1.0
    intercept = 0.0

    def apply(self, probability: float) -> float:
        return max(1e-9, min(1.0 - 1e-9, float(probability)))

    def to_dict(self) -> Dict[str, Any]:
        return {"slope": 1.0, "intercept": 0.0, "identity": True}


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_SELECTION_GUARD_V2_3_INSTALLED", False):
        return model_module
    feature_module = getattr(model_module, "features", None)
    required = (
        callable(getattr(model_module, "nested_select", None))
        and callable(getattr(model_module, "fit_residual_logistic", None))
        and callable(getattr(model_module, "fit_platt", None))
        and callable(getattr(model_module, "train_and_evaluate", None))
        and feature_module is not None
    )
    if not required:
        model_module._INQSI_MLB_SELECTION_GUARD_V2_3_INSTALLED = True
        return model_module

    feature_module.FEATURE_GROUPS = dict(feature_module.FEATURE_GROUPS)
    feature_module.FEATURE_GROUPS.setdefault(BASELINE_GROUP, tuple())
    original_fit = model_module.fit_residual_logistic
    original_platt = model_module.fit_platt
    original_train = model_module.train_and_evaluate

    def guarded_fit(examples: Sequence[Any], *, feature_group: str, l2: float, seed: int, **kwargs: Any) -> Any:
        if feature_group == BASELINE_GROUP:
            return MarketBaselineModel(model_module, seed=seed)
        return original_fit(examples, feature_group=feature_group, l2=l2, seed=seed, **kwargs)

    def guarded_platt(predictions: Sequence[float], outcomes: Sequence[int], **kwargs: Any) -> Any:
        if getattr(model_module, "_INQSI_MLB_IDENTITY_CALIBRATOR_ONCE", False):
            model_module._INQSI_MLB_IDENTITY_CALIBRATOR_ONCE = False
            return IdentityCalibrator()
        return original_platt(predictions, outcomes, **kwargs)

    model_module.fit_residual_logistic = guarded_fit
    model_module.fit_platt = guarded_platt

    def guarded_nested_select(examples: Sequence[Any], train_days: Sequence[str], *, seed: int = 260726) -> Dict[str, Any]:
        folds = model_module.inner_expanding_folds(train_days)
        l2_values = (0.02, 0.20)
        development = model_module._subset(examples, train_days)
        training_partitions = [model_module._subset(examples, inner_train_days) for inner_train_days, _ in folds]
        validation_partitions = [model_module._subset(examples, validation_days) for _, validation_days in folds]
        candidates: List[Dict[str, Any]] = []
        for group_index, (group, feature_names) in enumerate(feature_module.FEATURE_GROUPS.items()):
            group_coverage = feature_group_coverage(
                development,
                group=group,
                feature_names=feature_names,
                training_partitions=training_partitions,
                validation_partitions=validation_partitions,
            )
            for l2_index, l2 in enumerate(l2_values):
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
                        seed=seed + group_index * 1000 + l2_index * 100 + fold_index,
                        steps=220,
                    )
                    probabilities = [fitted.raw_probability(row) for row in validation]
                    metrics = model_module.evaluate_probabilities(validation, probabilities)
                    market = model_module._market_metrics(validation)
                    fold_rows.append({
                        "fold": fold_index + 1,
                        "trainFirstDate": min(inner_train_days),
                        "trainLastDate": max(inner_train_days),
                        "validationFirstDate": min(validation_days),
                        "validationLastDate": max(validation_days),
                        "metrics": metrics,
                        "marketBaseline": market,
                    })
                    all_probabilities.extend(probabilities)
                    all_outcomes.extend(row.outcome for row in validation)
                    all_examples.extend(validation)
                aggregate = model_module.evaluate_probabilities(all_examples, all_probabilities)
                market_aggregate = model_module._market_metrics(all_examples)
                if group == BASELINE_GROUP:
                    stability = {
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
                errors = list(group_coverage.get("errors") or []) + list(stability.get("errors") or [])
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
                candidate["selectionKey"] = _selection_key(candidate)
                candidates.append(candidate)
        selected = min(candidates, key=lambda row: tuple(row["selectionKey"]))
        model_module._INQSI_MLB_IDENTITY_CALIBRATOR_ONCE = selected["featureGroup"] == BASELINE_GROUP
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
        return {
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
                    "minimumOofAccuracyUplift": MIN_OOF_ACCURACY_UPLIFT,
                    "minimumOofNetCorrect": MIN_OOF_NET_CORRECT,
                    "maximumWorstFoldAccuracyRegression": MAX_WORST_FOLD_ACCURACY_REGRESSION,
                    "minimumPositiveFoldRatio": MIN_POSITIVE_FOLD_RATIO,
                    "minimumFundamentalsCoverage": MIN_FUNDAMENTALS_COVERAGE,
                    "minimumFundamentalsMetricCoverage": MIN_FUNDAMENTALS_METRIC_COVERAGE,
                    "minimumBbsPriorCoverage": MIN_BBS_PRIOR_COVERAGE,
                    "minimumV8MarketCoverage": MIN_V8_MARKET_COVERAGE,
                    "minimumV8FirstFiveCoverage": MIN_V8_FIRST_FIVE_COVERAGE,
                },
                "selectedCoverage": selected["guard"]["coverage"],
                "selectedStability": selected["guard"]["stability"],
                "learnedEligibleCandidateCount": sum(
                    item["featureGroup"] != BASELINE_GROUP and bool(item["guard"]["eligible"])
                    for item in candidates
                ),
            },
        }

    model_module.nested_select = guarded_nested_select

    def guarded_train(records: Sequence[Mapping[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        result = original_train(records, **kwargs)
        metrics = result.get("metrics") or {}
        market = metrics.get("marketBaseline") or {}
        gate = result.get("promotionGate") or {}
        errors = list(gate.get("errors") or [])
        for result_name, market_name, error_prefix in (
            ("walkForward", "walkForward", "walk_forward"),
            ("untouchedAudit", "untouchedAudit", "untouched_audit"),
        ):
            candidate_metrics = metrics.get(result_name) or {}
            market_metrics = market.get(market_name) or {}
            if _f(candidate_metrics.get("overallAccuracy")) + 1e-12 < _f(market_metrics.get("overallAccuracy")):
                errors.append(f"{error_prefix}_accuracy_worse_than_market")
        selection_guard = ((result.get("selection") or {}).get("selectionGuard") or {})
        if selection_guard.get("baselineFallbackUsed") is True:
            errors.append("no_stable_oof_uplift_over_market")
        gate["errors"] = sorted(set(errors))
        gate["passed"] = not gate["errors"]
        gate["selectionGuardVersion"] = VERSION
        result["promotionGate"] = gate
        result["learningStatus"] = (
            "MARKET_BASELINE_FALLBACK"
            if selection_guard.get("baselineFallbackUsed") is True
            else "STABLE_OOF_LEARNED_CANDIDATE"
        )
        digest = getattr(model_module, "_sha", None)
        if callable(digest):
            result["resultDigest"] = digest(
                {key: value for key, value in result.items() if key != "resultDigest"}
            )
        return result

    model_module.train_and_evaluate = guarded_train
    model_module._INQSI_MLB_SELECTION_GUARD_V2_3_INSTALLED = True
    return model_module
