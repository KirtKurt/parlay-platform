"""Runtime-bundle bound alignment for the MLB V8 observational audit.

The autonomous trainer publishes architecture-wide probability bounds, while an
independently refitted residual model may carry narrower fitted bounds. Runtime
bundle verification correctly requires the bundle contract to describe the fitted
model exactly. This layer preserves the trainer's source bounds as evidence and
advertises the fitted model's actual bounds in the observational runtime bundle.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

import mlb_v8_model_runtime as _runtime
import mlb_v8_observational_audit_v1 as _core
import mlb_v8_observational_audit_v1_3 as _base


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-v1.4-fitted-runtime-bounds"
_ORIGINAL_BUILD_CANDIDATE = _base.build_candidate


def align_runtime_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(dict(report))
    model = dict(value.get("model") or {})
    architecture = dict(value.get("architecture") or {})
    source_bounds = copy.deepcopy(architecture.get("probabilityBounds"))
    lower = float(model.get("minProbability", 0.05))
    upper = float(model.get("maxProbability", 0.95))
    if not (0.0 < lower < upper < 1.0):
        raise ValueError("observational fitted model probability bounds are invalid")

    # The runtime verifier compares the bundle contract with the serialized
    # fitted model.  Make both authorities explicit, including for model fakes
    # and legacy residuals that relied on the fitted-model defaults.
    model["minProbability"] = lower
    model["maxProbability"] = upper
    architecture["sourceTrainingProbabilityBounds"] = source_bounds
    architecture["probabilityBounds"] = [lower, upper]
    architecture["probabilityBoundsAuthority"] = "FITTED_OBSERVATIONAL_MODEL"
    value["model"] = model
    value["architecture"] = architecture
    return value


class _RuntimeAdapter:
    def build_bundle(self, report: Mapping[str, Any]) -> Dict[str, Any]:
        return _runtime.build_bundle(align_runtime_report(report))

    def verify_bundle(self, bundle: Mapping[str, Any]) -> None:
        _runtime.verify_bundle(bundle)

    def score(self, bundle: Mapping[str, Any], feature_values: Mapping[str, Any]):
        return _runtime.score(bundle, feature_values)


def build_candidate(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    kwargs["runtime_module"] = _RuntimeAdapter()
    training = kwargs.get("training")
    if training is None and args:
        training = args[0]
    candidate = _ORIGINAL_BUILD_CANDIDATE(*args, **kwargs)
    source_bounds = copy.deepcopy(
        ((training or {}).get("architecture") or {}).get("probabilityBounds")
    )
    candidate["sourceArchitectureProbabilityBounds"] = source_bounds
    candidate["runtimeProbabilityBounds"] = copy.deepcopy(
        (candidate.get("modelBundle") or {}).get("probabilityBounds")
    )
    candidate["candidateDigest"] = _core._sha(
        {key: item for key, item in candidate.items() if key != "candidateDigest"}
    )
    return candidate


_core.VERSION = VERSION
_base.VERSION = VERSION
_core.build_candidate = build_candidate
_base.build_candidate = build_candidate

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

globals()["VERSION"] = VERSION
globals()["align_runtime_report"] = align_runtime_report
globals()["build_candidate"] = build_candidate
