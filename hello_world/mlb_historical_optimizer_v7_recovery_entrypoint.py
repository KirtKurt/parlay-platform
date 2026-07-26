"""Historical recovery entrypoint with optional V8 Odds-market shadow metadata.

The separate historical optimizer remains isolated from production selection.
V8 can be enabled only through an explicit environment flag and remains
shadow-only until the existing chronological promotion gate passes.
"""
from __future__ import annotations

from typing import Any, Dict

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base
import mlb_odds_market_expansion_v8 as odds_market_v8

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v3-v8-shadow-packaged"

# Package and install the V8 normalizer patch. With MLB_V8_ENABLED=false it only
# recognizes already-present expanded markets and does not change provider cost.
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    migration = rematerialization.run_once()
    if migration is not None:
        migration.setdefault("version", VERSION)
        migration.setdefault("oddsMarketExpansion", odds_market_v8.shadow_contract())
        return migration

    base._append_authorized_range_extension()
    value = base.lambda_handler(event, context)
    if isinstance(value, dict):
        value.setdefault("oddsMarketExpansion", odds_market_v8.shadow_contract())
        value.setdefault("version", VERSION)
    return value
