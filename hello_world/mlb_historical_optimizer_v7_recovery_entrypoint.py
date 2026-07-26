"""Historical recovery entrypoint with optional V8 Odds-market shadow metadata.

The separate historical optimizer remains isolated from production selection.
V8 can be enabled only through an explicit environment flag and remains
shadow-only until the existing chronological promotion gate passes.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base
import mlb_odds_market_expansion_v8 as odds_market_v8

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v4-status-lease-bypass"

# Package and install the V8 normalizer patch. With MLB_V8_ENABLED=false it only
# recognizes already-present expanded markets and does not change provider cost.
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)


def _request_mode(event: Any) -> str:
    """Resolve the request mode using the canonical optimizer payload contract."""
    payload = base.optimizer_handler._payload(event)
    request_context = payload.get("requestContext") or {}
    http = request_context.get("http") if isinstance(request_context, Mapping) else {}
    method = str(
        payload.get("httpMethod")
        or (http.get("method") if isinstance(http, Mapping) else "")
        or ""
    ).upper()
    return str(payload.get("mode") or ("status" if method == "GET" else "orchestrate")).lower()


def _with_shadow_contract(value: Any) -> Any:
    if isinstance(value, dict):
        value.setdefault("oddsMarketExpansion", odds_market_v8.shadow_contract())
        value.setdefault("version", VERSION)
    return value


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Status is strictly read-only. Route it directly to the canonical handler so
    # it never competes for the rematerialization/orchestration lease and never
    # performs range-extension mutation before returning durable state.
    if _request_mode(event) == "status":
        return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))

    migration = rematerialization.run_once()
    if migration is not None:
        return _with_shadow_contract(migration)

    # The base entrypoint owns competitive-range repair and extension exactly
    # once. Do not invoke its range-extension helper separately here.
    return _with_shadow_contract(base.lambda_handler(event, context))
