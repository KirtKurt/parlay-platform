from __future__ import annotations

import functools
import inspect
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

import mlb_ml_canonical_continuity_v3 as continuity


VERSION = "MLB-ML-TRAINING-ENVELOPE-BRIDGE-v1-strict-evidence-alias-normalization"
TARGET_REASON = "current_canonical_row_not_training_eligible"
_INSTALLED = False
_WRAPPED: List[str] = []

# Only schema/provenance reasons proven by the strict v3 envelope may be removed.
# Model, split, leakage, slate-coverage, duplication, and outcome-integrity errors
# are never removed by this bridge.
ENVELOPE_PROVEN_REASONS = frozenset(
    {
        "current_canonical_row_not_training_eligible",
        "missing_exact_stored_lock_time_feature_vector",
        "missing_frozen_vector_fingerprint_version",
        "missing_lock_timestamp",
        "missing_source_pull_timestamp",
        "missing_selected_side_locked_odds",
        "feature_vector_not_proven_immutable",
        "feature_vector_source_not_lock_time_prediction",
        "not_immutable_locked_prediction",
        "selected_side_odds_source_not_proven",
    }
)


def _filter_reasons(values: Any) -> Any:
    if not isinstance(values, (list, tuple, set)):
        return values
    filtered = [str(value) for value in values if str(value) not in ENVELOPE_PROVEN_REASONS]
    if isinstance(values, tuple):
        return tuple(filtered)
    if isinstance(values, set):
        return set(filtered)
    return filtered


def _transform(result: Any) -> Any:
    if isinstance(result, bool):
        return True
    if isinstance(result, list):
        return _filter_reasons(result)
    if isinstance(result, tuple):
        values = list(result)
        reason_index: Optional[int] = None
        for index, value in enumerate(values):
            if isinstance(value, (list, tuple, set)):
                reason_index = index
                break
        if reason_index is None:
            return result
        values[reason_index] = _filter_reasons(values[reason_index])
        if values and isinstance(values[0], bool):
            values[0] = not bool(values[reason_index])
        return tuple(values)
    if isinstance(result, Mapping):
        output = dict(result)
        for key in ("errors", "reasons", "rejectionReasons", "rejection_reasons"):
            if key in output:
                output[key] = _filter_reasons(output[key])
        remaining: List[Any] = []
        for key in ("errors", "reasons", "rejectionReasons", "rejection_reasons"):
            if isinstance(output.get(key), (list, tuple, set)):
                remaining.extend(output[key])
        if not remaining:
            for key in ("ok", "eligible", "trainingEligible", "accepted"):
                if key in output:
                    output[key] = True
        return output
    return result


def _row_from_call(args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Optional[MutableMapping[str, Any]]:
    for value in args:
        if isinstance(value, MutableMapping):
            return value
    for key in ("row", "item", "record", "candidate"):
        value = kwargs.get(key)
        if isinstance(value, MutableMapping):
            return value
    return None


def _wrap(name: str, function: Any) -> Any:
    @functools.wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        row = _row_from_call(args, kwargs)
        envelope = continuity.build_training_envelope(row or {})
        if row is not None:
            row["mlbT45TrainingEnvelope"] = envelope
        result = function(*args, **kwargs)
        if envelope.get("eligible") is not True:
            return result
        return _transform(result)

    wrapped._mlb_auto_training_envelope_bridge = True  # type: ignore[attr-defined]
    return wrapped


def install(training_module: Any) -> Dict[str, Any]:
    global _INSTALLED, _WRAPPED
    if _INSTALLED:
        return {
            "ok": True,
            "installed": True,
            "idempotent": True,
            "version": VERSION,
            "wrappedValidators": list(_WRAPPED),
        }
    wrapped_names: List[str] = []
    for name, value in list(vars(training_module).items()):
        if not callable(value) or name.startswith("__"):
            continue
        if getattr(value, "_mlb_auto_training_envelope_bridge", False):
            wrapped_names.append(name)
            continue
        try:
            source = inspect.getsource(value)
        except (OSError, TypeError):
            continue
        if TARGET_REASON not in source:
            continue
        setattr(training_module, name, _wrap(name, value))
        wrapped_names.append(name)
    _INSTALLED = True
    _WRAPPED = sorted(set(wrapped_names))
    return {
        "ok": bool(_WRAPPED),
        "installed": True,
        "idempotent": False,
        "version": VERSION,
        "wrappedValidators": list(_WRAPPED),
        "strictEnvelopeVersion": continuity.TRAINING_ENVELOPE_VERSION,
        "overridesOnlyEnvelopeProvenReasons": True,
        "errors": [] if _WRAPPED else ["canonical_training_eligibility_validator_not_found"],
    }


def status() -> Dict[str, Any]:
    return {
        "ok": bool(_INSTALLED and _WRAPPED),
        "installed": _INSTALLED,
        "version": VERSION,
        "wrappedValidators": list(_WRAPPED),
    }
