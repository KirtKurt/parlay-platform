"""Supported-cohort and optional-signal aware selection guard for MLB V8.

V2.4 keeps every V2.3 stability and calibration requirement, but corrects two
coverage-contract defects:

* provider-limited BBD prior-game coverage is measured only inside the explicit
  provider-supported cohort, with absolute evidence floors for every fold; and
* full-game V8 feature groups are not rejected merely because the optional
  first-five market archive is unavailable.

No production, champion, cutover, or wagering authority is changed here.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence

try:
    import mlb_supervised_selection_guard_v2_3 as base
except ImportError:  # package import used by unit tests
    from . import mlb_supervised_selection_guard_v2_3 as base

VERSION = "MLB-SUPERVISED-SELECTION-GUARD-v2.4-supported-cohort"
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

    support_counts: Dict[str, Any] = {}
    if "bbs_prior" in group:
        overall_count = len(_supported(examples, BBS_SUPPORT_FEATURE))
        training_counts = [
            len(_supported(rows, BBS_SUPPORT_FEATURE)) for rows in training_partitions
        ]
        validation_counts = [
            len(_supported(rows, BBS_SUPPORT_FEATURE)) for rows in validation_partitions
        ]
        support_counts = {
            "feature": BBS_SUPPORT_FEATURE,
            "overall": overall_count,
            "trainingFolds": training_counts,
            "validationFolds": validation_counts,
            "minimumOverall": MIN_BBS_SUPPORTED_OVERALL_GAMES,
            "minimumTrainingFold": MIN_BBS_SUPPORTED_TRAIN_GAMES,
            "minimumValidationFold": MIN_BBS_SUPPORTED_VALIDATION_GAMES,
        }
        if overall_count < MIN_BBS_SUPPORTED_OVERALL_GAMES:
            errors.append("overall_bbs_supported_game_floor_not_met")
        for index, count in enumerate(training_counts, start=1):
            if count < MIN_BBS_SUPPORTED_TRAIN_GAMES:
                errors.append(f"train_fold_{index}_bbs_supported_game_floor_not_met")
        for index, count in enumerate(validation_counts, start=1):
            if count < MIN_BBS_SUPPORTED_VALIDATION_GAMES:
                errors.append(
                    f"validation_fold_{index}_bbs_supported_game_floor_not_met"
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
        "errors": sorted(set(errors)),
    }


MarketBaselineModel = base.MarketBaselineModel
IdentityCalibrator = base.IdentityCalibrator
candidate_stability = base.candidate_stability


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_SELECTION_GUARD_V2_4_INSTALLED", False):
        return model_module

    # V2.3's installer closes over these module globals. Replace only the
    # coverage contract and version before installation; all stability,
    # calibration, and market-fallback behavior remains unchanged.
    base.VERSION = VERSION
    base._coverage_requirements = _coverage_requirements
    base.feature_group_coverage = feature_group_coverage
    base.install(model_module)

    nested = getattr(model_module, "nested_select", None)
    if callable(nested) and not getattr(
        model_module, "_INQSI_MLB_SELECTION_GUARD_V2_4_METADATA_INSTALLED", False
    ):
        original_nested = nested

        def nested_with_v24_metadata(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            result = original_nested(*args, **kwargs)
            selection_guard = result.get("selectionGuard") or {}
            selection_guard["version"] = VERSION
            thresholds = selection_guard.get("thresholds") or {}
            thresholds.update(
                {
                    "minimumBbsSupportedOverallGames": MIN_BBS_SUPPORTED_OVERALL_GAMES,
                    "minimumBbsSupportedTrainingFoldGames": MIN_BBS_SUPPORTED_TRAIN_GAMES,
                    "minimumBbsSupportedValidationFoldGames": MIN_BBS_SUPPORTED_VALIDATION_GAMES,
                    "bbsCoverageDenominator": BBS_SUPPORT_FEATURE,
                    "v8FullGameRequiresFirstFive": False,
                }
            )
            selection_guard["thresholds"] = thresholds
            result["selectionGuard"] = selection_guard
            return result

        model_module.nested_select = nested_with_v24_metadata
        model_module._INQSI_MLB_SELECTION_GUARD_V2_4_METADATA_INSTALLED = True

    model_module._INQSI_MLB_SELECTION_GUARD_V2_4_INSTALLED = True
    return model_module
