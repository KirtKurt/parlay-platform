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
import mlb_historical_v7_learning_cadence_v1 as learning_cadence
import mlb_historical_v7_priority_repairs_v1 as priority_repairs
import mlb_odds_market_expansion_v8 as odds_market_v8
import mlb_supervised_v8_dataset_patch_v1 as supervised_v8_dataset

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v10-priority-repairs-active"

# Reopen only a terminal rejected state caused by the previous deployment ceiling.
round_extension.install(base.optimizer_handler)

# Normalize expanded markets already present in immutable historical payloads.
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)

# Promote expanded fields and provider event IDs into versioned side-signal metadata.
supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)

# Add separate starter/bullpen/lineup features and explicit missingness before V9
# creates defaults, bounds, and fitted coefficients for its feature list.
priority_repairs.install_feature_repairs(supervised_v9)

# Install strict labels and the deterministic supervised search.
supervised_integrity_v2.install(supervised_v9)
supervised_v9.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)

# Keep shadow-refit cadence separate from the canonical 200-game promotion audit.
learning_cadence.install(base.optimizer_handler, supervised_v9)


def _request_mode(event: Any) -> str:
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
                "featureCount": len(supervised_v9.FEATURES),
                "integrityPatchVersion": supervised_integrity_v2.VERSION,
                "learningCadenceVersion": learning_cadence.VERSION,
                "priorityRepairsVersion": priority_repairs.VERSION,
                "shadowRefitIncrementGames": priority_repairs.SHADOW_REFIT_INCREMENT_GAMES,
                "canonicalFreshAuditIncrementGames": base.optimizer_handler.FRESH_AUDIT_INCREMENT_GAMES,
                "shadowRefitsMayPromote": False,
                "strictBinaryLabels": True,
                "missingLabelsCoercedToAwayWin": False,
                "v8ExpansionFallbackEnabled": True,
                "sameSlateOutcomeFeaturesProhibited": True,
                "providerCallsRequiredForRematerialization": 0,
                "promotionRequiresEverySlateAtLeast80Pct": True,
                "freshProspectiveAuditRequiredBeforeProduction": True,
                "candidateHandoffRequiresCanonicalReevaluation": True,
                "separateFullSlateAndSelectiveAccuracy": True,
            },
        )
        value.setdefault("version", VERSION)
    return value


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    # Status is strictly read-only and does not compete for the optimizer lease.
    if _request_mode(event) == "status":
        return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))

    migration = rematerialization.run_once()
    if migration is not None:
        return _with_shadow_contract(migration)

    return _with_shadow_contract(base.lambda_handler(event, context))
