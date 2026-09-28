"""KS1 feature separation for shadow fundamentals challenger.

This does not alter KS1 serving authority or T-10 locks.
"""
from __future__ import annotations
from inqsi_intelligence.favorite_bias import assert_fundamentals_market_free

MARKET_FEATURES=frozenset({"market_home_prob","market_total","market_spread",
                           "edge_home","edge_total","odds_match_confidence"})

def fundamental_feature_names(feature_names):
    names=tuple(str(n) for n in feature_names if str(n) not in MARKET_FEATURES)
    assert_fundamentals_market_free(names)
    return names

def market_feature_names(feature_names):
    return tuple(str(n) for n in feature_names if str(n) in MARKET_FEATURES)
