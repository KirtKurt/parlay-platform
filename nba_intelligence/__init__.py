"""NBA independent-vs-market shadow layer.

Diagnostic only. Does not serve, lock, promote, or change NBA-B1.1C.1.
"""
from .contract import (
    ALLOWED_FUNDAMENTAL_FAMILIES,
    BANNED_MARKET_TOKENS,
    filter_fundamental_features,
)
from .layer import (
    credible_underdog,
    evaluate_game,
    explain_pick,
    vulnerable_favorite,
)
from .audit import ablation_compare, rolling_audit
from .markets import SUPPORTED_MARKETS, evaluate_market

AUTHORITY_CHANGED = False
