"""Tennis independent-intelligence contract.

Provides prediction-time market classification without altering tennis authority.
"""
from __future__ import annotations
from inqsi_intelligence.favorite_bias import compare_binary

def compare_match(*, event_id, player_a, player_b, p_fundamental_a,
                  p_market_aware_a, p_market_a):
    return compare_binary(event_id=str(event_id),
      p_fundamental_home=float(p_fundamental_a),
      p_market_aware_home=float(p_market_aware_a),
      p_market_home=float(p_market_a),home=str(player_a),away=str(player_b))
