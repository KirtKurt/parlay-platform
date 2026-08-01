"""Historical recovery entrypoint with independent V7 and V9 shadow learning.

V7 owns immutable odds-only learning and searches a selective PICK/PASS
objective. V8 remains the separate fundamentals ingestion path. V9 may consume
both later, but neither shadow learner can change production authority without
a fresh chronological audit and the canonical promotion write path.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import mlb_historical_feature_rematerialization_v1 as rematerialization
import mlb_historical_incremental_range_extension_v1 as incremental_range_extension
import mlb_historical_optimizer_entrypoint as base
import mlb_historical_rematerialization_waiting_repair_v1 as rematerialization_waiting_repair
import mlb_historical_round_extension_v1 as round_extension
import mlb_historical_state_integrity_v1 as state_integrity
import mlb_historical_supervised_v9 as supervised_v9
import mlb_historical_supervised_v9_integrity_v2 as supervised_integrity_v2
import mlb_historical_v7_context_features_v1 as context_features
import mlb_historical_v7_feature_bridge_v1 as feature_bridge
import mlb_historical_v7_label_integrity_v1 as label_integrity
import mlb_historical_v7_learning_cadence_v1 as learning_cadence
import mlb_historical_v7_prior_signal_bridge_v1 as prior_signal_bridge
import mlb_historical_v7_priority_repairs_v1 as priority_repairs
import mlb_historical_v7_selective_objective_v1 as selective_objective
import mlb_historical_v7_selective_search_v2 as selective_search_v2
import mlb_odds_market_expansion_v8 as odds_market_v8
import mlb_supervised_v8_dataset_patch_v1 as supervised_v8_dataset

VERSION = "MLB-HISTORICAL-V7-RECOVERY-ENTRYPOINT-v20-prior-context-bridge"

incremental_range_extension.install(base)
state_integrity.install(base.optimizer_handler, base)
round_extension.install(base.optimizer_handler)
odds_market_v8.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)
supervised_v8_dataset.install(base.optimizer_handler.optimizer, rematerialization)
rematerialization_waiting_repair.install(rematerialization)
prior_signal_bridge.install(feature_bridge)
priority_repairs.install_feature_repairs(supervised_v9)
context_features.install(supervised_v9)
label_integrity.install(supervised_v9)
supervised_integrity_v2.install(supervised_v9)
supervised_v9.install(base.optimizer_handler.optimizer, base.optimizer_handler.policy_runtime)
selective_objective.install(base.optimizer_handler.optimizer)
selective_search_v2.install(base.optimizer_handler.optimizer)
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
                "promotionRequiresEverySlateAtLeast80Pct": True,
                "promotionDailyAccuracyRequirement": base.optimizer_handler.policy_runtime.MIN_DAILY_ACCURACY,
                "datasetPatchVersion": supervised_v8_dataset.VERSION,
                "featureDatasetVersion": supervised_v8_dataset.FEATURE_DATASET_VERSION,
                "modelVersion": supervised_v9.VERSION,
                "featureVersion": supervised_v9.FEATURE_VERSION,
                "featureCount": len(supervised_v9.FEATURES),
                "contextFeaturesVersion": context_features.VERSION,
                "featureBridgeVersion": feature_bridge.VERSION,
                "priorSignalBridgeVersion": prior_signal_bridge.VERSION,
                "integrityPatchVersion": supervised_integrity_v2.VERSION,
                "labelIntegrityVersion": label_integrity.VERSION,
                "learningCadenceVersion": learning_cadence.VERSION,
                "priorityRepairsVersion": priority_repairs.VERSION,
                "selectiveObjectiveVersion": selective_objective.VERSION,
                "selectiveSearchVersion": selective_search_v2.VERSION,
                "shadowRefitIncrementGames": priority_repairs.SHADOW_REFIT_INCREMENT_GAMES,
                "lightweightSelectiveEvaluationIncrementGames": selective_search_v2.LIGHTWEIGHT_EVALUATION_INCREMENT_GAMES,
                "fullSelectiveSearchIncrementGames": selective_search_v2.FULL_SEARCH_INCREMENT_GAMES,
                "canonicalFreshAuditIncrementGames": base.optimizer_handler.FRESH_AUDIT_INCREMENT_GAMES,
                "shadowRefitsMayPromote": False,
                "strictBinaryLabels": True,
                "invalidOrMissingLabelsExcluded": True,
                "missingLabelsCoercedToAwayWin": False,
                "sameSlateOutcomeFeaturesProhibited": True,
                "providerCallsRequiredForRematerialization": 0,
                "v7InputAuthority": "ODDS_ONLY",
                "v8InputAuthority": "FUNDAMENTALS_ONLY",
                "objective": "selective_individual_game_accuracy",
                "pickPassEnabled": True,
                "jointPolicyThresholdReliabilitySearch": True,
                "calibrationTemperatureSearch": True,
                "regimeDiagnosticsEnabled": True,
                "thresholdStabilityRequired": True,
                "minimumSelectiveCoverage": selective_search_v2.MIN_COVERAGE,
                "minimumSelectiveWalkForwardPicks": selective_search_v2.MIN_WALK_FORWARD_PICKS,
                "minimumSelectiveUntouchedPicks": selective_search_v2.MIN_UNTOUCHED_PICKS,
                "productionSelectiveAccuracy": selective_search_v2.PRODUCTION_ACCURACY,
                "eliteSelectiveAccuracy": selective_search_v2.ELITE_ACCURACY,
                "thresholdFrozenBeforeUntouchedHoldout": True,
                "freshProspectiveAuditRequiredBeforeProduction": True,
                "candidateHandoffRequiresCanonicalReevaluation": True,
                "candidateHandoffFailsClosed": True,
                "separateFullSlateAndSelectiveAccuracy": True,
                "incrementalRangeExtensionVersion": incremental_range_extension.VERSION,
                "stateIntegrityVersion": state_integrity.VERSION,
                "settledHorizonWaitingPhase": state_integrity.WAITING_PHASE,
                "rangeExtensionRunsBeforeRematerialization": True,
                "rematerializationWaitingRepairVersion": rematerialization_waiting_repair.VERSION,
            },
        )
        value.setdefault("version", VERSION)
    return value


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    if _request_mode(event) == "status":
        return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))

    # Advance the settled-range ledger before feature rematerialization. Otherwise
    # a stale completion/error marker can consume the invocation and leave an
    # exhausted cursor outside the authorized plan for another full cycle.
    base._repair_precompetitive_extension_state()
    base._append_authorized_range_extension()

    migration = rematerialization.run_once()
    if migration is not None:
        return _with_shadow_contract(migration)
    return _with_shadow_contract(base.optimizer_handler.lambda_handler(event, context))
