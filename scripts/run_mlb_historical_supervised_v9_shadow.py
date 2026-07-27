#!/usr/bin/env python3
"""Evaluate the strict supervised historical learner against immutable AWS data.

Read-only: this script makes no provider request, mutates no DynamoDB/S3 state, and
cannot write a champion or production cutover. Promotion still requires the existing
chronological every-slate 80% gate and a fresh prospective audit.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _pct(value):
    return round(float(value or 0.0) * 100.0, 4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
    import mlb_historical_supervised_v9 as supervised_v9
    import mlb_historical_supervised_v9_integrity_v2 as integrity_v2

    handler = runtime.base.optimizer_handler
    integrity_v2.install(supervised_v9)
    supervised_v9.install(handler.optimizer, handler.policy_runtime)
    if not getattr(handler.optimizer, "_INQSI_MLB_SUPERVISED_V9_INSTALLED", False):
        raise RuntimeError("supervised V9 optimizer install did not complete")
    if not getattr(
        handler.optimizer,
        "_INQSI_MLB_SUPERVISED_INTEGRITY_V2_SEARCH_INSTALLED",
        False,
    ):
        raise RuntimeError("supervised V9 integrity search install did not complete")
    if not getattr(handler.policy_runtime, "_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED", False):
        raise RuntimeError("supervised V9 policy runtime install did not complete")

    state = handler._load_state()
    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    records = handler._load_training_records(state)
    config = handler.optimizer.SearchConfig(
        minimum_training_games=handler.policy_runtime.MIN_TRAINING_GAMES,
        minimum_walk_forward_games=handler.policy_runtime.MIN_WALK_FORWARD_GAMES,
        minimum_untouched_holdout_games=handler.policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        minimum_settled_games=handler.policy_runtime.MIN_TOTAL_SETTLED_GAMES,
        maximum_candidates=100,
        random_seed=1541,
    )
    result = handler.optimizer.search(records, config)
    latest = state.get("latestExperiment") or {}
    old_gate = latest.get("promotionGate") or {}
    gate = result.get("promotionGate") or {}
    diagnostics = result.get("supervisedDiagnostics") or {}
    training_integrity = result.get("trainingIntegrity") or {}
    report = {
        "proofType": "MLB_HISTORICAL_SUPERVISED_V9_SHADOW_EVALUATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
        ),
        "readOnly": True,
        "retrospectiveShadowOnly": True,
        "prospectiveAuditRequiredBeforePromotion": True,
        "providerCallsMade": 0,
        "productionAuthorityChanged": False,
        "historicalChampionWritten": False,
        "productionCutoverWritten": False,
        "runtimeInstall": {
            "supervisedOptimizerInstalled": bool(
                getattr(handler.optimizer, "_INQSI_MLB_SUPERVISED_V9_INSTALLED", False)
            ),
            "supervisedIntegrityInstalled": bool(
                getattr(
                    handler.optimizer,
                    "_INQSI_MLB_SUPERVISED_INTEGRITY_V2_SEARCH_INSTALLED",
                    False,
                )
            ),
            "supervisedPolicyRuntimeInstalled": bool(
                getattr(handler.policy_runtime, "_INQSI_MLB_SUPERVISED_V9_POLICY_INSTALLED", False)
            ),
            "modelVersion": supervised_v9.VERSION,
            "featureVersion": supervised_v9.FEATURE_VERSION,
            "integrityPatchVersion": integrity_v2.VERSION,
            "featureCount": len(supervised_v9.FEATURES),
        },
        "state": {
            "phase": state.get("phase"),
            "currentDate": state.get("currentDate"),
            "currentSlotIndex": state.get("currentSlotIndex"),
            "networkRequestCount": state.get("networkRequestCount"),
            "eligibleGameCount": state.get("eligibleGameCount"),
            "completeSlateCount": state.get("completeSlateCount"),
            "optimizationRound": state.get("optimizationRound"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "rematerializationComplete": state.get("featureRematerializationComplete"),
            "rematerializationErrors": state.get("featureRematerializationErrors") or [],
        },
        "trainingIntegrity": training_integrity,
        "priorCandidate": {
            "experimentId": latest.get("experimentId"),
            "status": latest.get("status"),
            "walkForwardMeanDailyAccuracyPct": _pct(old_gate.get("walkForwardMeanDailyAccuracy")),
            "walkForwardMinimumDailyAccuracyPct": _pct(old_gate.get("walkForwardMinimumDailyAccuracy")),
            "untouchedHoldoutMeanDailyAccuracyPct": _pct(old_gate.get("untouchedHoldoutMeanDailyAccuracy")),
            "untouchedHoldoutMinimumDailyAccuracyPct": _pct(old_gate.get("untouchedHoldoutMinimumDailyAccuracy")),
        },
        "supervisedCandidate": {
            "status": result.get("status"),
            "searchVersion": result.get("searchVersion"),
            "settledGameCount": result.get("settledGameCount"),
            "walkForwardMeanDailyAccuracyPct": _pct(gate.get("walkForwardMeanDailyAccuracy")),
            "walkForwardMinimumDailyAccuracyPct": _pct(gate.get("walkForwardMinimumDailyAccuracy")),
            "untouchedHoldoutMeanDailyAccuracyPct": _pct(gate.get("untouchedHoldoutMeanDailyAccuracy")),
            "untouchedHoldoutMinimumDailyAccuracyPct": _pct(gate.get("untouchedHoldoutMinimumDailyAccuracy")),
            "promotionPassed": gate.get("passed") is True,
            "errors": gate.get("errors") or [],
            "diagnostics": diagnostics,
        },
    }
    blockers = []
    if result.get("ok") is not True:
        blockers.append("supervised_search_failed")
    if training_integrity.get("rejected"):
        blockers.append("training_integrity_rejected_rows")
    if training_integrity.get("acceptedCount") != training_integrity.get("inputCount"):
        blockers.append("training_integrity_count_mismatch")
    if diagnostics.get("strictBinaryLabels") is not True:
        blockers.append("strict_binary_label_contract_missing")
    if diagnostics.get("v8ExpansionFallbackEnabled") is not True:
        blockers.append("v8_expansion_fallback_not_enabled")
    if diagnostics.get("randomPolicySearchDisabled") is not True:
        blockers.append("random_rule_search_not_disabled")
    if diagnostics.get("holdoutEvaluatedAfterFreeze") is not True:
        blockers.append("holdout_not_proven_post_freeze")
    if diagnostics.get("holdoutLabelsUsedForFitOrSelection") is not False:
        blockers.append("holdout_used_for_fit_or_selection")
    if state.get("featureRematerializationErrors"):
        blockers.append("feature_rematerialization_errors")
    report["blockers"] = blockers
    report["ok"] = not blockers
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
