from __future__ import annotations

import math
import os
from typing import Any, Mapping

from .ml import Model

MODEL_GUARD_VERSION = 'MLB_MODEL_INPUT_OOD_GUARD_V1'
FALLBACK_MODE = 'MARKET_BOOTSTRAP_OOD_FALLBACK'


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if math.isfinite(value) and value > 0 else float(default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except Exception:
        return int(default)


SINGLE_FEATURE_Z_THRESHOLD = _env_float('MLB_AUTO_OOD_SINGLE_Z', 6.0)
MULTI_FEATURE_Z_THRESHOLD = _env_float('MLB_AUTO_OOD_MULTI_Z', 4.0)
MULTI_FEATURE_COUNT = _env_int('MLB_AUTO_OOD_MULTI_COUNT', 2)
MAX_REPORTED_FEATURES = _env_int('MLB_AUTO_OOD_MAX_REPORTED_FEATURES', 12)


def policy_payload() -> dict[str, Any]:
    return {
        'enabled': True,
        'version': MODEL_GUARD_VERSION,
        'single_feature_z_threshold': SINGLE_FEATURE_Z_THRESHOLD,
        'multi_feature_z_threshold': MULTI_FEATURE_Z_THRESHOLD,
        'multi_feature_count': MULTI_FEATURE_COUNT,
        'fallback_mode': FALLBACK_MODE,
        'official_pick_blocked_when_triggered': True,
        'raw_model_probability_preserved': True,
    }


def _required_raw_features(name: str) -> tuple[str, ...]:
    if name.endswith('__sq'):
        return (name[:-4],)
    if '__x__' in name:
        left, right = name.split('__x__', 1)
        return left, right
    return (name,)


def _feature_value(row: Mapping[str, Any], name: str) -> float:
    if name.endswith('__sq'):
        base = name[:-4]
        value = float(row[base])
        return value * value
    if '__x__' in name:
        left, right = name.split('__x__', 1)
        return float(row[left]) * float(row[right])
    return float(row[name])


def _invalid_guard_result(
    *,
    reason: str,
    feature_names: list[str],
    invalid_features: list[str],
    scaler_metadata_available: bool,
) -> dict[str, Any]:
    return {
        **policy_payload(),
        'triggered': True,
        'fallback_required': True,
        'reason': reason,
        'features_evaluated': 0,
        'selected_feature_count': len(feature_names),
        'invalid_features': invalid_features[:MAX_REPORTED_FEATURES],
        'out_of_range_features': [],
        'max_abs_z': None,
        'scaler_metadata_available': scaler_metadata_available,
    }


def evaluate_model_input(model: Model | None, features: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed only when the champion is outside its learned input support.

    The model's own training means and scales are authoritative. A single extreme
    feature or several materially out-of-range features triggers a market-bootstrap
    fallback and blocks official-pick authority for that pull. Raw inputs are never
    clipped or rewritten.
    """
    if model is None:
        return {
            **policy_payload(),
            'triggered': False,
            'fallback_required': False,
            'reason': 'NO_CHAMPION',
            'features_evaluated': 0,
            'selected_feature_count': 0,
            'invalid_features': [],
            'out_of_range_features': [],
            'max_abs_z': None,
            'scaler_metadata_available': False,
        }

    names = [str(name) for name in model.feature_names]
    if not names:
        return _invalid_guard_result(
            reason='MODEL_HAS_NO_SELECTED_FEATURES',
            feature_names=names,
            invalid_features=['model.feature_names'],
            scaler_metadata_available=False,
        )

    metadata = model.metadata or {}
    means = metadata.get('feature_means') or {}
    scales = metadata.get('feature_scales') or {}
    if not isinstance(means, Mapping) or not isinstance(scales, Mapping):
        return _invalid_guard_result(
            reason='SCALER_METADATA_UNAVAILABLE',
            feature_names=names,
            invalid_features=names,
            scaler_metadata_available=False,
        )

    missing_scaler = [name for name in names if name not in means or name not in scales]
    if missing_scaler:
        return _invalid_guard_result(
            reason='SCALER_METADATA_UNAVAILABLE',
            feature_names=names,
            invalid_features=missing_scaler,
            scaler_metadata_available=False,
        )

    invalid: list[str] = []
    evaluated: list[dict[str, float | str]] = []
    for name in names:
        required = _required_raw_features(name)
        if any(raw_name not in features for raw_name in required):
            invalid.append(name)
            continue
        try:
            value = _feature_value(features, name)
            mean = float(means[name])
            scale = abs(float(scales[name]))
            if not all(math.isfinite(number) for number in (value, mean, scale)):
                raise ValueError('NON_FINITE')
            standardized = abs(value - mean) / max(1e-9, scale)
        except Exception:
            invalid.append(name)
            continue
        evaluated.append({
            'name': name,
            'value': value,
            'training_mean': mean,
            'training_scale': scale,
            'abs_z': standardized,
        })

    if invalid:
        return _invalid_guard_result(
            reason='INVALID_OR_MISSING_SELECTED_FEATURE',
            feature_names=names,
            invalid_features=invalid,
            scaler_metadata_available=True,
        )

    single_extreme = [row for row in evaluated if float(row['abs_z']) > SINGLE_FEATURE_Z_THRESHOLD]
    multi_extreme = [row for row in evaluated if float(row['abs_z']) > MULTI_FEATURE_Z_THRESHOLD]
    triggered = bool(single_extreme or len(multi_extreme) >= MULTI_FEATURE_COUNT)
    if single_extreme:
        reason = 'SINGLE_FEATURE_EXTREME'
        breaches = single_extreme
    elif len(multi_extreme) >= MULTI_FEATURE_COUNT:
        reason = 'MULTIPLE_FEATURES_OUT_OF_RANGE'
        breaches = multi_extreme
    else:
        reason = 'IN_RANGE'
        breaches = []

    ordered = sorted(breaches, key=lambda row: float(row['abs_z']), reverse=True)
    reported = [
        {
            'name': str(row['name']),
            'value': float(row['value']),
            'training_mean': float(row['training_mean']),
            'training_scale': float(row['training_scale']),
            'abs_z': round(float(row['abs_z']), 6),
        }
        for row in ordered[:MAX_REPORTED_FEATURES]
    ]
    max_abs_z = max((float(row['abs_z']) for row in evaluated), default=0.0)
    return {
        **policy_payload(),
        'triggered': triggered,
        'fallback_required': triggered,
        'reason': reason,
        'features_evaluated': len(evaluated),
        'selected_feature_count': len(names),
        'invalid_features': [],
        'out_of_range_features': reported,
        'max_abs_z': round(max_abs_z, 6),
        'scaler_metadata_available': True,
    }
