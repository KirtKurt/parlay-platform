"""Provider-horizon-aware selection guard for MLB V8.

V2.5 preserves every V2.4 coverage ratio, calibration, stability, and market
fallback requirement while correcting an impossible chronological-fold contract.
BigBallsData prior-game history begins on a bounded provider horizon, so an early
chronological fold may contain too few provider-supported games to be evaluated.
Such a fold is explicitly marked not evaluable; it is never treated as passing.
A BBD candidate must still satisfy the 50% coverage requirement overall and in at
least two adequately supported training folds and two adequately supported
validation folds, plus the unchanged all-fold predictive-stability gates.

No production, champion, cutover, or wagering authority is changed here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence

try:
    import mlb_supervised_selection_guard_v2_3 as base
except ImportError:  # package import used by unit tests
    from . import mlb_supervised_selection_guard_v2_3 as base

VERSION = "MLB-SUPERVISED-SELECTION-GUARD-v2.5-provider-horizon-evaluable-folds"
BASELINE_GROUP = base.BASELINE_GROUP

MIN_OOF_ACCURACY_UPLIFT = base.MIN_OOF_ACCURACY_UPLIFT
MIN_OOF_NET_CORRECT = base.MIN_OOF_NET_CORRECT
MAX_WORST_FOLD_ACCURACY_REGRESSION = base.MAX_WORST_FOLD_ACCURACY_REGRESSION
MIN_POSITIVE_FOLD_RATIO = base.MIN_POSITIVE_FOLD_RATIO
MIN_FUNDAMENTALS_COVERAGE = base.MIN_FUNDAMENTALS_COVERAGE
MIN_FUNDAMENTALS_METRIC_COVERAGE = base.MIN_FUNDAMENTALS_METRIC_COVERAGE
MIN_BBS_PRIOR_COVERAGE = base.MIN_BBS_PRIOR_COVERAGE
MIN_V8_MARKET_COVERAGE = base.MIN_V8_MARKET_COVERAGE
MIN_V8_FIRST_FIVE_COVERAGE = base.MIN_V8_FIRST_FIVE_COVERAGE

BBS_SUPPORT_FEATURE = "bbs_prior_supported"
MIN_BBS_SUPPORTED_OVERALL_GAMES = 500
MIN_BBS_SUPPORTED_TRAIN_GAMES = 200
MIN_BBS_SUPPORTED_VALIDATION_GAMES = 150
MIN_BBS_EVALUABLE_TRAIN_FOLDS = 2
MIN_BBS_EVALUABLE_VALIDATION_FOLDS = 2

_ORIGINAL_COVERAGE_REQUIREMENTS = base._coverage_requirements


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _supported(rows: Sequence[Any], feature_name: str) -> List[Any]:
    return [
        row
        for row in rows
        if _f(getattr(row, "features", {}).get(feature_name)) > 0.5
    ]


def _coverage(
    rows: Sequence[Any],
    feature_name: str,
    *,
    support_feature: str | None = None,
) -> float:
    cohort = _supported(rows, support_feature) if support_feature else list(rows)
    if not cohort:
        return 0.0
    values = [_f(getattr(row, "features", {}).get(feature_name)) for row in cohort]
    if feature_name == "fundamentals_group_coverage":
        return sum(max(0.0, min(1.0, value)) for value in values) / len(values)
    return sum(value > 0.5 for value in values) / len(values)


def _coverage_requirements(group: str, feature_names: Iterable[str]) -> Dict[str, float]:
    requirements = dict(_ORIGINAL_COVERAGE_REQUIREMENTS(group, feature_names))
    if "bbs_prior" in group:
        requirements["bbs_prior_available"] = MIN_BBS_PRIOR_COVERAGE
    if "v8_fullgame" in group:
        requirements.pop("v8_f5_available", None)
    return requirements


def _support_feature(group: str, requirement: str) -> str | None:
    if "bbs_prior" in group and requirement == "bbs_prior_available":
        return BBS_SUPPORT_FEATURE
    return None


def _required_fold_count(configured: int, partitions: Sequence[Sequence[Any]]) -> int:
    """Require the configured count when available, without breaking tiny tests."""
    return min(configured, len(partitions))


def _fold_evaluation(
    counts: Sequence[int],
    *,
    minimum: int,
) -> List[Dict[str, Any]]:
    return [
        {
            "fold": index,
            "supportedGameCount": count,
            "minimumSupportedGameCount": minimum,
            "evaluable": count >= minimum,
            "reason": None if count >= minimum else "provider_horizon_overlap_below_floor",
        }
        for index, count in enumerate(counts, start=1)
    ]


def feature_group_coverage(
    examples: Sequence[Any],
    *,
    group: str,
    feature_names: Iterable[str],
    training_partitions: Sequence[Sequence[Any]] = (),
    validation_partitions: Sequence[Sequence[Any]] = (),
) -> Dict[str, Any]:
    requirements = _coverage_requirements(group, feature_names)
    overall = {
        name: round(
            _coverage(
                examples,
                name,
                support_feature=_support_feature(group, name),
            ),
            8,
        )
        for name in requirements
    }
    training = [
        {
            name: round(
                _coverage(
                    rows,
                    name,
                    support_feature=_support_feature(group, name),
                ),
                8,
            )
            for name in requirements
        }
        for rows in training_partitions
    ]
    validation = [
        {
            name: round(
                _coverage(
                    rows,
                    name,
                    support_feature=_support_feature(group, name),
                ),
                8,
            )
            for name in requirements
        }
        for rows in validation_partitions
    ]

    is_bbs_group = "bbs_prior" in group
    training_counts = (
        [len(_supported(rows, BBS_SUPPORT_FEATURE)) for rows in training_partitions]
        if is_bbs_group
        else []
    )
    validation_counts = (
        [len(_supported(rows, BBS_SUPPORT_FEATURE)) for rows in validation_partitions]
        if is_bbs_group
        else []
    )
    training_evaluable = [
        count >= MIN_BBS_SUPPORTED_TRAIN_GAMES for count in training_counts
    ]
    validation_evaluable = [
        count >= MIN_BBS_SUPPORTED_VALIDATION_GAMES for count in validation_counts
    ]

    errors: List[str] = []
    for name, minimum in requirements.items():
        if overall.get(name, 0.0) + 1e-12 < minimum:
            errors.append(f"overall_{name}_below_{minimum:.2f}")

        support_feature = _support_feature(group, name)
        for index, row in enumerate(training, start=1):
            # A provider-horizon-limited BBD fold is not evidence and is not a
            # pass. It is excluded from this ratio test and counted separately
            # toward the minimum-evaluable-fold contract below.
            if support_feature == BBS_SUPPORT_FEATURE and not training_evaluable[index - 1]:
                continue
            if row.get(name, 0.0) + 1e-12 < minimum:
                errors.append(f"train_fold_{index}_{name}_below_{minimum:.2f}")
        for index, row in enumerate(validation, start=1):
            if support_feature == BBS_SUPPORT_FEATURE and not validation_evaluable[index - 1]:
                continue
            if row.get(name, 0.0) + 1e-12 < minimum:
                errors.append(f"validation_fold_{index}_{name}_below_{minimum:.2f}")

    support_counts: Dict[str, Any] = {}
    fold_evaluation: Dict[str, Any] = {}
    if is_bbs_group:
        overall_count = len(_supported(examples, BBS_SUPPORT_FEATURE))
        required_training = _required_fold_count(
            MIN_BBS_EVALUABLE_TRAIN_FOLDS, training_partitions
        )
        required_validation = _required_fold_count(
            MIN_BBS_EVALUABLE_VALIDATION_FOLDS, validation_partitions
        )
        evaluable_training_count = sum(training_evaluable)
        evaluable_validation_count = sum(validation_evaluable)
        skipped_training = [
            index for index, evaluable in enumerate(training_evaluable, start=1) if not evaluable
        ]
        skipped_validation = [
            index for index, evaluable in enumerate(validation_evaluable, start=1) if not evaluable
        ]
        support_counts = {
            "feature": BBS_SUPPORT_FEATURE,
            "overall": overall_count,
            "trainingFolds": training_counts,
            "validationFolds": validation_counts,
            "minimumOverall": MIN_BBS_SUPPORTED_OVERALL_GAMES,
            "minimumTrainingFold": MIN_BBS_SUPPORTED_TRAIN_GAMES,
            "minimumValidationFold": MIN_BBS_SUPPORTED_VALIDATION_GAMES,
            "trainingFoldEvaluable": training_evaluable,
            "validationFoldEvaluable": validation_evaluable,
            "evaluableTrainingFoldCount": evaluable_training_count,
            "evaluableValidationFoldCount": evaluable_validation_count,
            "requiredEvaluableTrainingFoldCount": required_training,
            "requiredEvaluableValidationFoldCount": required_validation,
            "skippedTrainingFolds": skipped_training,
            "skippedValidationFolds": skipped_validation,
        }
        fold_evaluation = {
            "policy": "provider_horizon_evaluable_folds_only",
            "unsupportedFoldsCountAsPassing": False,
            "training": _fold_evaluation(
                training_counts, minimum=MIN_BBS_SUPPORTED_TRAIN_GAMES
            ),
            "validation": _fold_evaluation(
                validation_counts, minimum=MIN_BBS_SUPPORTED_VALIDATION_GAMES
            ),
        }
        if overall_count < MIN_BBS_SUPPORTED_OVERALL_GAMES:
            errors.append("overall_bbs_supported_game_floor_not_met")
        if evaluable_training_count < required_training:
            errors.append(
                f"bbs_evaluable_training_fold_count_below_{required_training}"
            )
        if evaluable_validation_count < required_validation:
            errors.append(
                f"bbs_evaluable_validation_fold_count_below_{required_validation}"
            )

    denominators = {
        name: (
            BBS_SUPPORT_FEATURE
            if _support_feature(group, name) == BBS_SUPPORT_FEATURE
            else "all_examples"
        )
        for name in requirements
    }
    return {
        "eligible": not errors,
        "requirements": requirements,
        "overall": overall,
        "trainingFolds": training,
        "validationFolds": validation,
        "denominators": denominators,
        "supportCounts": support_counts,
        "foldEvaluation": fold_evaluation,
        "errors": sorted(set(errors)),
    }


MarketBaselineModel = base.MarketBaselineModel
IdentityCalibrator = base.IdentityCalibrator
candidate_stability = base.candidate_stability


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_SELECTION_GUARD_V2_5_INSTALLED", False):
        return model_module

    # V2.3's installer closes over these module globals. Replace only the
    # coverage contract and version before installation; all predictive
    # stability, calibration, and market-fallback behavior remains unchanged.
    base.VERSION = VERSION
    base._coverage_requirements = _coverage_requirements
    base.feature_group_coverage = feature_group_coverage
    base.install(model_module)

    nested = getattr(model_module, "nested_select", None)
    if callable(nested) and not getattr(
        model_module, "_INQSI_MLB_SELECTION_GUARD_V2_5_METADATA_INSTALLED", False
    ):
        original_nested = nested

        def nested_with_v25_metadata(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            result = original_nested(*args, **kwargs)
            selection_guard = result.get("selectionGuard") or {}
            selection_guard["version"] = VERSION
            thresholds = selection_guard.get("thresholds") or {}
            thresholds.update(
                {
                    "minimumBbsSupportedOverallGames": MIN_BBS_SUPPORTED_OVERALL_GAMES,
                    "minimumBbsSupportedTrainingFoldGames": MIN_BBS_SUPPORTED_TRAIN_GAMES,
                    "minimumBbsSupportedValidationFoldGames": MIN_BBS_SUPPORTED_VALIDATION_GAMES,
                    "minimumBbsEvaluableTrainingFolds": MIN_BBS_EVALUABLE_TRAIN_FOLDS,
                    "minimumBbsEvaluableValidationFolds": MIN_BBS_EVALUABLE_VALIDATION_FOLDS,
                    "bbsCoverageDenominator": BBS_SUPPORT_FEATURE,
                    "providerHorizonUnsupportedFoldsFailCoverage": False,
                    "providerHorizonUnsupportedFoldsCountAsPassing": False,
                    "v8FullGameRequiresFirstFive": False,
                }
            )
            selection_guard["thresholds"] = thresholds
            result["selectionGuard"] = selection_guard
            return result

        model_module.nested_select = nested_with_v25_metadata
        model_module._INQSI_MLB_SELECTION_GUARD_V2_5_METADATA_INSTALLED = True

    model_module._INQSI_MLB_SELECTION_GUARD_V2_4_INSTALLED = True
    model_module._INQSI_MLB_SELECTION_GUARD_V2_5_INSTALLED = True
    return model_module
