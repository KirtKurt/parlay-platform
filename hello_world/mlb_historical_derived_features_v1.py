"""Leakage-safe derived-feature learning for the MLB historical optimizer.

This module expands the optimizer beyond manually weighted raw signals.  It derives
nonlinear and interaction features exclusively from each game's existing T-minus-45
clipped signal, exposes bounded coefficients to candidate search, and applies the
same formula in compiled historical evaluation and live champion scoring.
"""
from __future__ import annotations

import copy
import math
import random
from typing import Any, Dict, Mapping, Sequence, Tuple

VERSION = "MLB-HISTORICAL-DERIVED-FEATURES-v1-lock-bounded-search-and-runtime-parity"

DERIVED_POLICY_DEFAULTS: Dict[str, float] = {
    "derivedMovementSqrtWeight": 0.0,
    "derivedAgreementMomentumWeight": 0.0,
    "derivedVelocityInteractionWeight": 0.0,
    "derivedAccelerationInteractionWeight": 0.0,
    "derivedInstabilityPenalty": 0.0,
    "derivedVelocityGapWeight": 0.0,
}

DERIVED_POLICY_BOUNDS: Dict[str, Tuple[float, float]] = {
    "derivedMovementSqrtWeight": (-0.20, 0.20),
    "derivedAgreementMomentumWeight": (-4.0, 4.0),
    "derivedVelocityInteractionWeight": (-0.10, 0.10),
    "derivedAccelerationInteractionWeight": (-0.05, 0.05),
    "derivedInstabilityPenalty": (0.0, 0.20),
    "derivedVelocityGapWeight": (-0.05, 0.05),
}

DERIVED_POLICY_CHOICES: Dict[str, Sequence[float]] = {
    "derivedMovementSqrtWeight": (-0.08, -0.04, 0.0, 0.04, 0.08),
    "derivedAgreementMomentumWeight": (-1.5, -0.75, 0.0, 0.75, 1.5, 2.5),
    "derivedVelocityInteractionWeight": (-0.04, -0.02, 0.0, 0.02, 0.04),
    "derivedAccelerationInteractionWeight": (-0.02, -0.01, 0.0, 0.01, 0.02),
    "derivedInstabilityPenalty": (0.0, 0.01, 0.025, 0.05, 0.08),
    "derivedVelocityGapWeight": (-0.02, -0.01, 0.0, 0.01, 0.02),
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except Exception:
        return default


def _nested(mapping: Any, *path: str) -> Any:
    value = mapping
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def derive(signal: Mapping[str, Any]) -> Dict[str, float]:
    """Create deterministic features from lock-bounded inputs only."""
    delta = _f(signal.get("delta"), 0.0)
    divergence = max(0.0, _f(signal.get("bookDivergence"), 0.0))
    reversals = max(0.0, _f(signal.get("reversalCount"), 0.0))
    coverage = min(
        1.0,
        max(
            0.0,
            _f(_nested(signal, "temporalFeatures", "horizons", "full", "coverageRatio"), 0.0),
        ),
    )
    velocity60 = _f(
        _nested(signal, "temporalFeatures", "horizons", "60m", "velocityPpHr"), 0.0
    )
    velocity_full = _f(
        _nested(signal, "temporalFeatures", "horizons", "full", "velocityPpHr"), 0.0
    )
    acceleration180 = _f(
        _nested(signal, "temporalFeatures", "horizons", "180m", "accelerationPpHr2"), 0.0
    )
    volatility180 = max(
        0.0,
        _f(
            _nested(
                signal,
                "temporalFeatures",
                "horizons",
                "180m",
                "volatilityPpPerPull",
            ),
            0.0,
        ),
    )
    direction = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
    agreement = max(0.0, 1.0 - min(1.0, divergence / 0.075))
    values = {
        "movementSqrt": direction * math.sqrt(abs(delta)),
        "agreementMomentum": delta * agreement * coverage,
        "velocityInteraction": delta * velocity60,
        "accelerationInteraction": delta * acceleration180,
        "instabilityInteraction": abs(delta) * volatility180 * (1.0 + min(5.0, reversals)),
        "velocityGap": velocity60 - velocity_full,
    }
    return {name: round(value, 12) for name, value in values.items()}


def adjustment(signal: Mapping[str, Any], policy: Mapping[str, Any]) -> float:
    features = derive(signal)
    value = (
        features["movementSqrt"] * _f(policy.get("derivedMovementSqrtWeight"))
        + features["agreementMomentum"]
        * _f(policy.get("derivedAgreementMomentumWeight"))
        + features["velocityInteraction"]
        * _f(policy.get("derivedVelocityInteractionWeight"))
        + features["accelerationInteraction"]
        * _f(policy.get("derivedAccelerationInteractionWeight"))
        - features["instabilityInteraction"]
        * _f(policy.get("derivedInstabilityPenalty"))
        + features["velocityGap"] * _f(policy.get("derivedVelocityGapWeight"))
    )
    # A derived feature may influence the score, but no candidate can create an
    # unbounded override of the underlying market and fixed-rule model.
    return max(-0.12, min(0.12, value))


def _signal_values(signal: Mapping[str, Any]) -> Tuple[float, ...]:
    features = derive(signal)
    return (
        features["movementSqrt"],
        features["agreementMomentum"],
        features["velocityInteraction"],
        features["accelerationInteraction"],
        features["instabilityInteraction"],
        features["velocityGap"],
    )


def _policy_values(policy: Mapping[str, Any]) -> Tuple[float, ...]:
    return tuple(_f(policy.get(name)) for name in DERIVED_POLICY_DEFAULTS)


def _compiled_adjustment(signal: Tuple[Any, ...], policy: Tuple[float, ...]) -> float:
    features = signal[-6:]
    weights = policy[-6:]
    value = (
        features[0] * weights[0]
        + features[1] * weights[1]
        + features[2] * weights[2]
        + features[3] * weights[3]
        - features[4] * weights[4]
        + features[5] * weights[5]
    )
    return max(-0.12, min(0.12, value))


def install(optimizer: Any, policy_runtime: Any) -> None:
    """Install derived feature search and runtime scoring exactly once."""
    if getattr(optimizer, "_INQSI_DERIVED_FEATURES_V1_INSTALLED", False):
        return

    policy_runtime.BASELINE_POLICY.update(
        {
            name: policy_runtime.BASELINE_POLICY.get(name, default)
            for name, default in DERIVED_POLICY_DEFAULTS.items()
        }
    )
    policy_runtime._NUMERIC_BOUNDS.update(DERIVED_POLICY_BOUNDS)

    original_signal = optimizer._signal
    original_candidate_policy = optimizer._candidate_policy
    original_compile_signal = optimizer._compile_signal_for_search
    original_compile_policy = optimizer._compile_policy_for_search
    original_score_compiled = optimizer._score_compiled_signal
    original_production_optimized = policy_runtime.production_optimized_signal

    def patched_signal(game, observations, side, expected_slots):
        out = original_signal(game, observations, side, expected_slots)
        out["derivedFeatures"] = derive(out)
        out["derivedFeatureVersion"] = VERSION
        out["derivedFeatureSource"] = "existing_game_t_minus_45_clipped_signal_only"
        return out

    def patched_candidate_policy(rng: random.Random):
        candidate = original_candidate_policy(rng)
        for name, values in DERIVED_POLICY_CHOICES.items():
            candidate[name] = rng.choice(values)
        return candidate

    def patched_compile_signal(signal):
        return tuple(original_compile_signal(signal)) + _signal_values(signal)

    def patched_compile_policy(policy):
        return tuple(original_compile_policy(policy)) + _policy_values(policy)

    def patched_score_compiled(signal, policy):
        base_score, _ = original_score_compiled(tuple(signal[:16]), tuple(policy[:27]))
        score = max(0.0, min(100.0, base_score + _compiled_adjustment(signal, policy) * 100.0))
        probability = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
        return round(score, 4), round(max(0.05, min(0.95, probability)), 8)

    def patched_production_optimized_signal(signal, policy):
        out = original_production_optimized(signal, policy)
        derived = derive(signal)
        derived_adjustment = adjustment(signal, policy)
        base_score = _f(out.get("optimizedWinnerScore"), 50.0)
        score = max(0.0, min(100.0, base_score + derived_adjustment * 100.0))
        probability = 1.0 / (1.0 + math.exp(-(score - 50.0) / 12.0))
        out.update(
            {
                "derivedFeatures": derived,
                "derivedFeatureVersion": VERSION,
                "derivedFeatureScoreAdjustment": round(derived_adjustment * 100.0, 6),
                "derivedFeatureProbabilityAdjustment": round(derived_adjustment, 8),
                "optimizedWinnerScore": round(score, 4),
                "score": round(score, 4),
                "winProbability": round(max(0.05, min(0.95, probability)), 8),
                "winProbabilityPct": round(max(0.05, min(0.95, probability)) * 100.0, 4),
            }
        )
        return out

    optimizer._signal = patched_signal
    optimizer._candidate_policy = patched_candidate_policy
    optimizer._compile_signal_for_search = patched_compile_signal
    optimizer._compile_policy_for_search = patched_compile_policy
    optimizer._score_compiled_signal = patched_score_compiled
    policy_runtime.production_optimized_signal = patched_production_optimized_signal
    optimizer.DERIVED_FEATURE_VERSION = VERSION
    optimizer._INQSI_DERIVED_FEATURES_V1_INSTALLED = True
