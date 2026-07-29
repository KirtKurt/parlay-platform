"""Seed-aligned regularization search for the MLB V8 shadow challenger.

V2.6 preserves the V2.5 provider-horizon coverage contract and every calibration,
repeatable-uplift, fold-stability, market-fallback, and promotion requirement. It
repairs optimizer comparison noise by evaluating every regularization value for a
given feature group and chronological fold with the same deterministic training
shuffle. It also broadens the bounded L2 grid so a valid residual is not rejected
merely because only two regularization strengths were attempted.

No production, champion, cutover, or wagering authority is changed here.
"""
from __future__ import annotations

from typing import Any, Dict

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


def install(model_module: Any) -> Any:
    if getattr(model_module, "_INQSI_MLB_SELECTION_GUARD_V2_6_INSTALLED", False):
        return model_module

    # V2.3 owns the selector implementation used by later guards. Configure its
    # bounded grid before V2.5 installs the coverage- and stability-aware selector.
    base.REGULARIZATION_GRID = tuple(REGULARIZATION_GRID)
    prior.VERSION = VERSION
    prior.install(model_module)

    nested = getattr(model_module, "nested_select", None)
    if callable(nested) and not getattr(
        model_module, "_INQSI_MLB_SELECTION_GUARD_V2_6_METADATA_INSTALLED", False
    ):
        original_nested = nested

        def nested_with_v26_metadata(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            result = original_nested(*args, **kwargs)
            selection_guard = result.get("selectionGuard") or {}
            selection_guard["version"] = VERSION
            thresholds = selection_guard.get("thresholds") or {}
            thresholds.update(
                {
                    "regularizationGrid": list(REGULARIZATION_GRID),
                    "regularizationComparisonSeedAligned": True,
                    "regularizationGridBounded": True,
                }
            )
            selection_guard["thresholds"] = thresholds
            result["selectionGuard"] = selection_guard
            return result

        model_module.nested_select = nested_with_v26_metadata
        model_module._INQSI_MLB_SELECTION_GUARD_V2_6_METADATA_INSTALLED = True

    model_module._INQSI_MLB_SELECTION_GUARD_V2_6_INSTALLED = True
    return model_module
