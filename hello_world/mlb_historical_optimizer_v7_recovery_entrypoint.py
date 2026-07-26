"""Historical recovery entrypoint with supervised V9 shadow training.

The separate historical optimizer remains isolated from production selection.
The supervised challenger replaces randomized rule-only search inside this
historical runtime, while production authority remains V7 unless the unchanged
chronological 80%-every-day promotion gate passes.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base
import mlb_historical_round_extension_v1 as round_extension
import mlb_historical_supervised_v9 as supervised_v9
import mlb_odds_market_expansion_v8 as odds_market_v8

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v6-supervised-v9-shadow"
FEATURE_DATASET_VERSION = "MLB-HISTORICAL-FEATURE-DATASET-v9-supervised-v8-trainable"
REMATERIALIZATION_VERSION = (
    "MLB-HISTORICAL-FEATURE-REMATERIALIZATION-v2-supervised-v9-v8-trainable"
)

# Reopen only a terminal rejected state that was caused by the previous six-round
# deployment ceiling. The patch requires a strictly later untouched-audit start
# and leaves every prior experiment and promotion decision immutable.
round_extension.install(base.optimizer_handler)

# Install the V8 normalizer before rebuilding historical datasets. Historical
# snapshots that contain expanded markets receive trainable V8 fields; snapshots
# collected under the older H2H-only contract remain explicit missing values.
odds_market_v8.install(
    base.optimizer_handler.optimizer,
    base.optimizer_handler.policy_runtime,
)

# Replace the 25,000-policy randomized rule sweep with the nested chronological,
# day-balanced supervised challenger. The production gate and authority records
# remain unchanged and fail closed.
supervised_v9.install(
    base.optimizer_handler.optimizer,
    base.optimizer_handler.policy_runtime,
)

# Force a no-provider-call rebuild of every completed slate under the supervised
# feature contract. The content-addressed dataset writer installed by the base
# entrypoint prevents any prior evidence from being overwritten.
rematerialization.VERSION = REMATERIALIZATION_VERSION
rematerialization.FEATURE_DATASET_VERSION = FEATURE_DATASET_VERSION
rematerialization.BATCH_SIZE = 5


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
        value.setdefault(
            "supervisedHistoricalChallenger",
            {
                "version": supervised_v9.VERSION,
                "featureVersion": supervised_v9.FEATURE_VERSION,
                "featureDatasetVersion": FEATURE_DATASET_VERSION,
                "authority": "SHADOW_ONLY_UNTIL_EXISTING_GATE_PASSES",
                "randomPolicySearchDisabled": True,
                "productionAuthorityChanged": False,
            },
        )
        value.setdefault("version", VERSION)
    return value


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Status is strictly read-only. Route it directly to the canonical handler so
    # it never competes for the rematerialization/orchestration lease.
    if _request_mode(event) == "status":
        return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))

    migration = rematerialization.run_once()
    if migration is not None:
        return _with_shadow_contract(migration)

    # The base entrypoint owns competitive-range repair and extension exactly
    # once. Do not invoke its range-extension helper separately here.
    return _with_shadow_contract(base.lambda_handler(event, context))
