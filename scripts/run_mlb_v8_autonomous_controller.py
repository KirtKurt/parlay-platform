#!/usr/bin/env python3
"""Build the durable decision for the single MLB V8 autonomy controller."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

VERSION = "MLB-V8-AUTONOMOUS-CONTROLLER-v2-prospective-state"
NONFATAL_BLOCKERS = frozenset({"context_backfill_retry_required"})


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


def _digest_valid(value: Mapping[str, Any], field: str) -> bool:
    expected = str(value.get(field) or "")
    if not expected:
        return False
    actual = _sha({key: item for key, item in value.items() if key != field})
    return expected == actual


def _hard_blockers(blockers: list[str]) -> list[str]:
    return [item for item in blockers if item not in NONFATAL_BLOCKERS]


def decide(
    *,
    training: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    prospective_audit: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    context = context or {}
    prospective_audit = prospective_audit or {}
    created = datetime.now(timezone.utc).isoformat()
    learning = training.get("learningExecution") or {}
    autonomy = training.get("autonomy") or {}
    context_present = bool(context)
    context_ok = context.get("ok") is True if context_present else None
    audit_present = bool(prospective_audit)
    audit_ok = prospective_audit.get("ok") is True if audit_present else None
    audit_digest_valid = (
        _digest_valid(prospective_audit, "lifecycleDigest")
        if audit_present and audit_ok
        else False
    )
    training_ok = training.get("ok") is True
    digest_valid = (
        _digest_valid(training, "resultDigest") if training_ok else False
    )
    learning_executed = learning.get("learningExecuted") is True
    provider_neutral = training.get("historicalBbsRequired") is False
    automatic_wager_disabled = training.get("automaticWagerAllowed") is False
    requested = str(training.get("autonomyDecision") or "")
    audit_action = str(prospective_audit.get("action") or "")

    blockers: list[str] = []
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
    if not audit_present:
        blockers.append("prospective_audit_report_missing")
    elif audit_ok is not True:
        blockers.append("prospective_audit_report_unhealthy")
    elif not audit_digest_valid:
        blockers.append("prospective_audit_digest_invalid")
    if prospective_audit.get("automaticWagerAllowed") is not False:
        blockers.append("prospective_audit_wager_safety_invalid")
    if prospective_audit.get("productionAuthorityChanged") is not False:
        blockers.append("prospective_audit_changed_production_authority")
    if prospective_audit.get("modelRefitDuringProspectiveAudit") is not False:
        blockers.append("prospective_audit_refit_detected")
    if prospective_audit.get("selectionUsedProspectiveOutcomes") is not False:
        blockers.append("prospective_outcome_selection_detected")

    hard = _hard_blockers(blockers)
    if hard:
        next_action = "REPAIR_AND_RETRY_AUTONOMOUS_TRAINING"
    elif requested == "AUTO_PROMOTE_GUARDED_CHAMPION":
        next_action = requested
    elif audit_action in {
        "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT",
        "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH",
    }:
        next_action = audit_action
    elif context_present and context_ok is False:
        next_action = "RETRY_CONTEXT_BACKFILL_AND_CONTINUE_TRAINING"
    elif requested:
        next_action = requested
    else:
        next_action = "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"

    promotion_requested = next_action == "AUTO_PROMOTE_GUARDED_CHAMPION"
    fully_autonomous = bool(
        autonomy.get("contextBackfillAutomatic") is True
        and autonomy.get("candidateTrainingAutomatic") is True
        and autonomy.get("chronologicalValidationAutomatic") is True
        and autonomy.get("prospectiveAuditAutomatic") is True
        and autonomy.get("guardedChampionPromotionAutomatic") is True
        and autonomy.get("postPromotionVerificationAutomatic") is True
        and autonomy.get("rollbackOnVerificationFailureAutomatic") is True
        and audit_present
        and audit_ok is True
        and audit_digest_valid
    )
    result = {
        "proofType": "MLB_V8_AUTONOMOUS_CONTROLLER",
        "version": VERSION,
        "createdAtUtc": created,
        "ok": not hard,
        "fullyAutonomous": fully_autonomous,
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
        "prospectiveAudit": {
            "reportPresent": audit_present,
            "ok": audit_ok,
            "lifecycleDigestValid": audit_digest_valid,
            "status": prospective_audit.get("status"),
            "action": audit_action or None,
            "candidateDigest": prospective_audit.get("candidateDigest"),
            "modelDigest": prospective_audit.get("modelDigest"),
            "frozenCorpusLastDate": prospective_audit.get(
                "frozenCorpusLastDate"
            ),
            "prospectiveEvidenceComplete": prospective_audit.get(
                "prospectiveEvidenceComplete"
            ),
            "prospectiveAuditPassed": prospective_audit.get(
                "prospectiveAuditPassed"
            ),
            "prospectiveAuditRejected": prospective_audit.get(
                "prospectiveAuditRejected"
            ),
            "modelRefitDuringAudit": prospective_audit.get(
                "modelRefitDuringProspectiveAudit"
            ),
            "selectionUsedProspectiveOutcomes": prospective_audit.get(
                "selectionUsedProspectiveOutcomes"
            ),
        },
        "requestedAction": requested or None,
        "nextAction": next_action,
        "contextBackfillRetryScheduled": bool(
            context_present and context_ok is False
        ),
        "prospectiveAuditCollectionScheduled": next_action
        == "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT",
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
    parser.add_argument("--prospective-audit", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    training = _load(Path(args.training_report))
    context = _load(Path(args.context_report)) if args.context_report else {}
    prospective_audit = _load(Path(args.prospective_audit))
    result = decide(
        training=training,
        context=context,
        prospective_audit=prospective_audit,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
