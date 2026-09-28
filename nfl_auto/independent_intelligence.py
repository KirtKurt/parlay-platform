"""NFL adapter for independent-vs-market diagnostics. No serving authority."""
from __future__ import annotations
from .features import FEATURE_NAMES
from inqsi_intelligence.favorite_bias import assert_fundamentals_market_free

MARKET_FEATURES=frozenset({
 "market_line","market_dispersion","bookmaker_count_scaled",
 "probability_move_24h_to_t10","probability_move_60m_to_t10",
 "line_move_24h_to_t10","line_move_60m_to_t10",
})
FUNDAMENTAL_FEATURE_NAMES=tuple(n for n in FEATURE_NAMES if n not in MARKET_FEATURES)

def split_feature_vector(values):
    if len(values)!=len(FEATURE_NAMES):
        raise ValueError("NFL feature vector length mismatch")
    row=dict(zip(FEATURE_NAMES,map(float,values)))
    fundamentals={k:row[k] for k in FUNDAMENTAL_FEATURE_NAMES}
    market={k:row[k] for k in FEATURE_NAMES if k in MARKET_FEATURES}
    assert_fundamentals_market_free(fundamentals)
    return {"fundamentals":fundamentals,"market":market}
