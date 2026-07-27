#!/usr/bin/env python3
"""Read-only strict V7 shadow evaluation against immutable AWS evidence."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _pct(value):
    return round(float(value or 0.0) * 100.0, 4)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous-report")
    parser.add_argument("--handoff-output")
    args = parser.parse_args()

    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
    import mlb_historical_supervised_v9 as supervised_v9
    import mlb_historical_supervised_v9_integrity_v2 as integrity_v2
    import mlb_historical_v7_priority_repairs_v1 as repairs

    handler = runtime.base.optimizer_handler
    integrity_v2.install(supervised_v9)
    supervised_v9.install(handler.optimizer, handler.policy_runtime)
    state = handler._load_state()
    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    records = handler._load_training_records(state)
    fingerprint = repairs.dataset_fingerprint(records)
    previous = _load_json(Path(args.previous_report)) if args.previous_report else {}
    previous_count = int((previous.get("state") or {}).get("eligibleGameCount") or 0)
    current_count = int(state.get("eligibleGameCount") or len(records))
    previous_fingerprint = str(previous.get("datasetFingerprint") or "")
    new_games = max(0, current_count - previous_count)
    threshold = int(os.environ.get("MLB_V7_SHADOW_REFIT_INCREMENT_GAMES", "50"))
    force = os.environ.get("MLB_V7_FORCE_SHADOW_REFIT", "false").lower() == "true"
    should_refit = force or not previous_fingerprint or (
        fingerprint != previous_fingerprint and new_games >= threshold
    )

    base_report = {
        "proofType": "MLB_HISTORICAL_SUPERVISED_V9_SHADOW_EVALUATION",
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "readOnly": True,
        "retrospectiveShadowOnly": True,
        "prospectiveAuditRequiredBeforePromotion": True,
        "providerCallsMade": 0,
        "productionAuthorityChanged": False,
        "historicalChampionWritten": False,
        "productionCutoverWritten": False,
        "datasetFingerprint": fingerprint,
        "shadowRefitIncrementGames": threshold,
        "canonicalFreshAuditIncrementGames": handler.FRESH_AUDIT_INCREMENT_GAMES,
        "newEligibleGamesSinceLastShadowFit": new_games,
        "state": {
            "phase": state.get("phase"),
            "currentDate": state.get("currentDate"),
            "currentSlotIndex": state.get("currentSlotIndex"),
            "eligibleGameCount": current_count,
            "completeSlateCount": state.get("completeSlateCount"),
            "optimizationRound": state.get("optimizationRound"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "rematerializationComplete": state.get("featureRematerializationComplete"),
            "rematerializationErrors": state.get("featureRematerializationErrors") or [],
        },
        "featurePopulation": repairs.feature_population_report(
            records, supervised_v9, handler.policy_runtime.BASELINE_POLICY
        ),
        "operationsDiagnostics": repairs.rejection_and_lease_report(state, handler),
        "accuracyViews": repairs.selective_accuracy_report(records),
    }

    if not should_refit:
        base_report.update(
            {
                "ok": True,
                "shadowRefitPerformed": False,
                "stalledStage": "WAITING_FOR_50_NEW_ELIGIBLE_GAMES",
                "blockers": [],
                "previousShadowDatasetFingerprint": previous_fingerprint,
            }
        )
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(base_report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(base_report, indent=2, sort_keys=True))
        return 0

    config = handler.optimizer.SearchConfig(
        minimum_training_games=handler.policy_runtime.MIN_TRAINING_GAMES,
        minimum_walk_forward_games=handler.policy_runtime.MIN_WALK_FORWARD_GAMES,
        minimum_untouched_holdout_games=handler.policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        minimum_settled_games=handler.policy_runtime.MIN_TOTAL_SETTLED_GAMES,
        maximum_candidates=100,
        random_seed=1541,
    )
    result = handler.optimizer.search(records, config)
    gate = result.get("promotionGate") or {}
    diagnostics = result.get("supervisedDiagnostics") or {}
    integrity = result.get("trainingIntegrity") or {}
    handoff = repairs.candidate_handoff(result, fingerprint)
    if args.handoff_output:
        handoff_path = Path(args.handoff_output)
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")

    blockers = []
    if result.get("ok") is not True:
        blockers.append("supervised_search_failed")
    if integrity.get("rejected"):
        blockers.append("training_integrity_rejected_rows")
    if integrity.get("acceptedCount") != integrity.get("inputCount"):
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

    base_report.update(
        {
            "ok": not blockers,
            "shadowRefitPerformed": True,
            "blockers": blockers,
            "trainingIntegrity": integrity,
            "runtimeInstall": {
                "modelVersion": supervised_v9.VERSION,
                "featureVersion": supervised_v9.FEATURE_VERSION,
                "featureCount": len(supervised_v9.FEATURES),
                "priorityRepairsVersion": repairs.VERSION,
            },
            "supervisedCandidate": {
                "status": result.get("status"),
                "searchVersion": result.get("searchVersion"),
                "settledGameCount": result.get("settledGameCount"),
                "walkForwardMeanDailyAccuracyPct": _pct(
                    gate.get("walkForwardMeanDailyAccuracy")
                ),
                "walkForwardMinimumDailyAccuracyPct": _pct(
                    gate.get("walkForwardMinimumDailyAccuracy")
                ),
                "untouchedHoldoutMeanDailyAccuracyPct": _pct(
                    gate.get("untouchedHoldoutMeanDailyAccuracy")
                ),
                "untouchedHoldoutMinimumDailyAccuracyPct": _pct(
                    gate.get("untouchedHoldoutMinimumDailyAccuracy")
                ),
                "brierScore": gate.get("brierScore") or diagnostics.get("brierScore"),
                "logLoss": gate.get("logLoss") or diagnostics.get("logLoss"),
                "promotionPassed": gate.get("passed") is True,
                "errors": gate.get("errors") or [],
                "diagnostics": diagnostics,
            },
            "canonicalCandidateHandoff": handoff,
        }
    )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base_report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(base_report, indent=2, sort_keys=True))
    return 0 if base_report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
