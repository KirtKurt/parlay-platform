#!/usr/bin/env python3
"""Build the durable decision for the single MLB V8 autonomy controller."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

VERSION = "MLB-V8-AUTONOMOUS-CONTROLLER-v1"


def _load(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.exists() or not path.stat().st_size:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"controller input is not an object:{path}")
    return value


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _training_digest_valid(training: Mapping[str, Any]) -> bool:
    expected = str(training.get("resultDigest") or "")
    if not expected:
        return False
    actual = _sha(
        {key: item for key, item in training.items() if key != "resultDigest"}
    )
    return expected == actual


def decide(
    *,
    training: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    context = context or {}
    created = datetime.now(timezone.utc).isoformat()
    learning = training.get("learningExecution") or {}
    autonomy = training.get("autonomy") or {}
    context_present = bool(context)
    context_ok = context.get("ok") is True if context_present else None
    training_ok = training.get("ok") is True
    digest_valid = _training_digest_valid(training) if training_ok else False
    learning_executed = learning.get("learningExecuted") is True
    provider_neutral = training.get("historicalBbsRequired") is False
    automatic_wager_disabled = training.get("automaticWagerAllowed") is False
    requested = str(training.get("autonomyDecision") or "")

    blockers = []
    if not training_ok:
        blockers.append("training_report_unhealthy")
    if training_ok and not digest_valid:
        blockers.append("training_result_digest_invalid")
    if not learning_executed:
        blockers.append("candidate_learning_execution_unproven")
    if not provider_neutral:
        blockers.append("retired_provider_still_required")
    if not automatic_wager_disabled:
        blockers.append("automatic_wager_not_disabled")
    if context_present and context_ok is False:
        blockers.append("context_backfill_retry_required")

    if any(
        item
        for item in blockers
        if item != "context_backfill_retry_required"
    ):
        next_action = "REPAIR_AND_RETRY_AUTONOMOUS_TRAINING"
    elif requested == "AUTO_PROMOTE_GUARDED_CHAMPION":
        next_action = requested
    elif context_present and context_ok is False:
        next_action = "RETRY_CONTEXT_BACKFILL_AND_CONTINUE_TRAINING"
    elif requested:
        next_action = requested
    else:
        next_action = "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"

    promotion_requested = next_action == "AUTO_PROMOTE_GUARDED_CHAMPION"
    result = {
        "proofType": "MLB_V8_AUTONOMOUS_CONTROLLER",
        "version": VERSION,
        "createdAtUtc": created,
        "ok": not any(
            item
            for item in blockers
            if item != "context_backfill_retry_required"
        ),
        "fullyAutonomous": bool(
            autonomy.get("contextBackfillAutomatic") is True
            and autonomy.get("candidateTrainingAutomatic") is True
            and autonomy.get("chronologicalValidationAutomatic") is True
            and autonomy.get("prospectiveAuditAutomatic") is True
            and autonomy.get("guardedChampionPromotionAutomatic") is True
            and autonomy.get("postPromotionVerificationAutomatic") is True
            and autonomy.get("rollbackOnVerificationFailureAutomatic") is True
        ),
        "normalOperationManualInterventionRequired": False,
        "humanEmergencyOverrideAvailable": True,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
        "context": {
            "reportPresent": context_present,
            "latestRunOk": context_ok,
            "provider": context.get("provider"),
            "processedGameCount": context.get("processedGameCount"),
            "eligibleGameCount": context.get("eligibleGameCount"),
            "bbsApiUsed": context.get("bbsApiUsed"),
            "productionAuthorityChanged": context.get(
                "productionAuthorityChanged"
            ),
        },
        "training": {
            "ok": training_ok,
            "resultDigestValid": digest_valid,
            "recordCountLoaded": training.get("recordCountLoaded"),
            "learningStatus": training.get("learningStatus"),
            "learningExecuted": learning_executed,
            "totalOptimizationSteps": learning.get("totalOptimizationSteps"),
            "learnedCandidateCount": learning.get("learnedCandidateCount"),
            "learnedEligibleCandidateCount": learning.get(
                "learnedEligibleCandidateCount"
            ),
            "selectedFeatureGroup": learning.get("selectedFeatureGroup"),
            "marketBaselineRetainedByGuard": learning.get(
                "marketBaselineRetainedByGuard"
            ),
            "promotionGatePassed": (
                training.get("promotionGate") or {}
            ).get("passed"),
            "freshProspectiveAuditRequired": training.get(
                "freshProspectiveAuditRequired"
            ),
            "productionPromotionEligible": training.get(
                "productionPromotionEligible"
            ),
        },
        "requestedAction": requested or None,
        "nextAction": next_action,
        "contextBackfillRetryScheduled": bool(
            context_present and context_ok is False
        ),
        "promotionRequested": promotion_requested,
        "verificationRequiredAfterPromotion": promotion_requested,
        "rollbackRequiredOnVerificationFailure": promotion_requested,
        "blockers": blockers,
    }
    result["controllerDigest"] = _sha(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training-report", required=True)
    parser.add_argument("--context-report")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    training = _load(Path(args.training_report))
    context = _load(Path(args.context_report)) if args.context_report else {}
    result = decide(training=training, context=context)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
