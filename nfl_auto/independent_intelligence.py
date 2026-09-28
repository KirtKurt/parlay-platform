"""NFL adapter for independent-vs-market diagnostics. No serving authority."""
from __future__ import annotations

from typing import Sequence

from .features import FEATURE_NAMES
from inqsi_intelligence.favorite_bias import assert_fundamentals_market_free

MARKET_FEATURES = frozenset({
    "market_line",
    "market_dispersion",
    "bookmaker_count_scaled",
    "probability_move_24h_to_t10",
    "probability_move_60m_to_t10",
    "line_move_24h_to_t10",
    "line_move_60m_to_t10",
})
FUNDAMENTAL_FEATURE_NAMES = tuple(name for name in FEATURE_NAMES if name not in MARKET_FEATURES)
MARKET_FEATURE_INDEXES = tuple(
    index for index, name in enumerate(FEATURE_NAMES) if name in MARKET_FEATURES
)
NEUTRAL_PRIOR = 0.5


def split_feature_vector(values):
    if len(values) != len(FEATURE_NAMES):
        raise ValueError("NFL feature vector length mismatch")
    row = dict(zip(FEATURE_NAMES, map(float, values)))
    fundamentals = {key: row[key] for key in FUNDAMENTAL_FEATURE_NAMES}
    market = {key: row[key] for key in FEATURE_NAMES if key in MARKET_FEATURES}
    assert_fundamentals_market_free(fundamentals)
    return {"fundamentals": fundamentals, "market": market}


def zero_market_slots(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != len(FEATURE_NAMES):
        raise ValueError("NFL feature vector length mismatch")
    cleaned = [float(value) for value in values]
    for index in MARKET_FEATURE_INDEXES:
        cleaned[index] = 0.0
    return tuple(cleaned)


def fundamentals_only_probability(model, features: Sequence[float]) -> float:
    """Diagnostic ablation. Does not change ResidualLogisticModel.predict_probability.

    Book columns are zeroed and market_prior is replaced with a coin-flip prior so
    the published residual path cannot be reused as a fake fundamentals model.
    """
    return float(model.predict_probability(zero_market_slots(features), NEUTRAL_PRIOR))
