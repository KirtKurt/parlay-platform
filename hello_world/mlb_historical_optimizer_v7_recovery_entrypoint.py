"""Historical recovery entrypoint with trainable V8 and supervised metadata.

The historical optimizer evaluates the supervised challenger directly, but production
selection remains protected by the existing chronological every-slate 80% promotion
gate. Expanded V8 fields and strict training-integrity evidence remain shadow-only
until that gate and a fresh prospective audit pass.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_optimizer_entrypoint as base
import mlb_historical_round_extension_v1 as round_extension
import mlb_historical_supervised_v9 as supervised_v9
import mlb_historical_supervised_v9_integrity_v2 as supervised_integrity_v2
import mlb_odds_market_expansion_v8 as odds_market_v8
import mlb_supervised_v8_dataset_patch_v1 as supervised_v8_dataset

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v8-supervised-v9-integrity-active"

# Reopen only a terminal rejected state that was caused by the previous deployment
# ceiling. Prior experiments and promotion decisions remain immutable.
round_extension.install(base.optimizer_handler)

# Normalize expanded markets already present in immutable historical payloads.
# This performs no additional provider request and does not grant V8 authority.
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)

# Promote expanded fields and provider event IDs into versioned side-signal metadata.
# Missing historical fields remain explicit; rematerialization makes zero provider calls.
supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)

# Install strict labels, V8 payload fallback and richer pre-lock pattern interactions
# before activating the supervised search. The active historical search can produce a
# challenger, but cannot create production authority unless the existing 80%-every-day
# walk-forward and untouched-audit gate passes.
supervised_integrity_v2.install(supervised_v9)
supervised_v9.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)


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
                "authorityMayChangeOnlyAfterPromotionGate": True,
                "productionAuthorityChanged": False,
                "datasetPatchVersion": supervised_v8_dataset.VERSION,
                "featureDatasetVersion": supervised_v8_dataset.FEATURE_DATASET_VERSION,
                "modelVersion": supervised_v9.VERSION,
                "featureVersion": supervised_v9.FEATURE_VERSION,
                "integrityPatchVersion": supervised_integrity_v2.VERSION,
                "strictBinaryLabels": True,
                "missingLabelsCoercedToAwayWin": False,
                "v8ExpansionFallbackEnabled": True,
                "sameSlateOutcomeFeaturesProhibited": True,
                "providerCallsRequiredForRematerialization": 0,
                "promotionRequiresEverySlateAtLeast80Pct": True,
                "freshProspectiveAuditRequiredBeforeProduction": True,
            },
        )
        value.setdefault("version", VERSION)
    return value


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Status is strictly read-only. Route it directly to the canonical handler so it
    # never competes for the rematerialization/orchestration lease.
    if _request_mode(event) == "status":
        return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))

    migration = rematerialization.run_once()
    if migration is not None:
        return _with_shadow_contract(migration)

    # The base entrypoint owns competitive-range repair and extension exactly once.
    return _with_shadow_contract(base.lambda_handler(event, context))
