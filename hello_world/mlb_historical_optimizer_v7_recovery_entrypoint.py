"""Historical recovery entrypoint with trainable V8 shadow metadata.

The separate historical optimizer remains isolated from production selection.
Expanded V8 market fields are propagated into rematerialized lock-bounded records
for supervised shadow evaluation, but production authority remains unchanged until
the existing chronological promotion gate passes.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base
import mlb_historical_round_extension_v1 as round_extension
import mlb_odds_market_expansion_v8 as odds_market_v8
import mlb_supervised_v8_dataset_patch_v1 as supervised_v8_dataset

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v7-supervised-v8-rematerialization-proof"

# Reopen only a terminal rejected state that was caused by the previous six-round
# deployment ceiling. The patch requires a strictly later untouched-audit start
# and leaves every prior experiment and promotion decision immutable.
round_extension.install(base.optimizer_handler)

# Normalize any expanded markets already present in immutable historical payloads.
# This performs no additional provider request and does not grant V8 authority.
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)

# Promote V8 fields from event shadow metadata into versioned side-signal features
# for the supervised challenger. Missing historical fields stay explicit and zero
# provider calls are made during rematerialization.
supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)


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
            "supervisedShadow",
            {
                "authority": "SHADOW_ONLY",
                "productionAuthorityChanged": False,
                "datasetPatchVersion": supervised_v8_dataset.VERSION,
                "featureDatasetVersion": supervised_v8_dataset.FEATURE_DATASET_VERSION,
                "sameSlateOutcomeFeaturesProhibited": True,
                "providerCallsRequiredForRematerialization": 0,
            },
        )
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
