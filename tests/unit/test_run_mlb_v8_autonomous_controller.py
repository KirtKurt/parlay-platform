from hello_world import mlb_v8_autonomy_v1 as autonomy
import run_mlb_v8_autonomous_controller as controller


def _training(*, decision="CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"):
    value = {
        "ok": True,
        "learningExecution": {
            "learningExecuted": True,
            "totalOptimizationSteps": 1000,
            "learnedCandidateCount": 10,
            "learnedEligibleCandidateCount": 0,
            "selectedFeatureGroup": "market_baseline",
            "marketBaselineRetainedByGuard": True,
        },
        "autonomy": {
            "contextBackfillAutomatic": True,
            "candidateTrainingAutomatic": True,
            "chronologicalValidationAutomatic": True,
            "prospectiveAuditAutomatic": True,
            "guardedChampionPromotionAutomatic": True,
            "postPromotionVerificationAutomatic": True,
            "rollbackOnVerificationFailureAutomatic": True,
        },
        "historicalBbsRequired": False,
        "automaticWagerAllowed": False,
        "autonomyDecision": decision,
        "promotionGate": {"passed": False},
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
        "recordCountLoaded": 4099,
    }
    value["resultDigest"] = autonomy._sha(value)
    return value


def _audit(
    *,
    action="CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH",
    status="WAITING_FOR_RETROSPECTIVE_GATE",
):
    value = {
        "ok": True,
        "status": status,
        "action": action,
        "candidateDigest": None,
        "modelDigest": None,
        "prospectiveEvidenceComplete": False,
        "prospectiveAuditPassed": False,
        "prospectiveAuditRejected": False,
        "modelRefitDuringProspectiveAudit": False,
        "selectionUsedProspectiveOutcomes": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    value["lifecycleDigest"] = autonomy._sha(value)
    return value


def test_healthy_training_is_fully_autonomous_without_promotion():
    result = controller.decide(
        training=_training(),
        context={
            "ok": True,
            "provider": "official_mlb_plus_internal_canonical_context",
            "bbsApiUsed": False,
            "productionAuthorityChanged": False,
        },
        prospective_audit=_audit(),
    )

    assert result["ok"] is True
    assert result["fullyAutonomous"] is True
    assert result["normalOperationManualInterventionRequired"] is False
    assert result["nextAction"] == "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"
    assert result["promotionRequested"] is False
    assert result["prospectiveAudit"]["lifecycleDigestValid"] is True


def test_context_failure_schedules_retry_without_stalling_learning():
    result = controller.decide(
        training=_training(),
        context={"ok": False, "bbsApiUsed": False},
        prospective_audit=_audit(),
    )

    assert result["ok"] is True
    assert "context_backfill_retry_required" in result["blockers"]
    assert result["contextBackfillRetryScheduled"] is True
    # Every controller cycle always runs context before training.  The durable
    # next model action can therefore remain candidate search while the context
    # retry is independently scheduled by the same controller.
    assert result["nextAction"] == "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"


def test_collecting_candidate_controls_next_action():
    result = controller.decide(
        training=_training(),
        context={"ok": True, "bbsApiUsed": False},
        prospective_audit=_audit(
            action="COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT",
            status="COLLECTING",
        ),
    )

    assert result["ok"] is True
    assert result["prospectiveAuditCollectionScheduled"] is True
    assert result["nextAction"] == "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT"


def test_promotion_decision_requires_verification_and_rollback():
    result = controller.decide(
        training=_training(decision="AUTO_PROMOTE_GUARDED_CHAMPION"),
        context={"ok": True, "bbsApiUsed": False},
        prospective_audit=_audit(
            action="AUTO_PROMOTE_GUARDED_CHAMPION", status="PASSED"
        ),
    )

    assert result["promotionRequested"] is True
    assert result["verificationRequiredAfterPromotion"] is True
    assert result["rollbackRequiredOnVerificationFailure"] is True


def test_invalid_training_digest_fails_closed():
    training = _training()
    training["resultDigest"] = "tampered"
    result = controller.decide(
        training=training,
        context={"ok": True},
        prospective_audit=_audit(),
    )

    assert result["ok"] is False
    assert "training_result_digest_invalid" in result["blockers"]
    assert result["nextAction"] == "REPAIR_AND_RETRY_AUTONOMOUS_TRAINING"


def test_missing_or_tampered_prospective_state_fails_closed():
    missing = controller.decide(training=_training(), context={"ok": True})
    assert missing["ok"] is False
    assert "prospective_audit_report_missing" in missing["blockers"]

    tampered_audit = _audit()
    tampered_audit["status"] = "PASSED"
    tampered = controller.decide(
        training=_training(),
        context={"ok": True},
        prospective_audit=tampered_audit,
    )
    assert tampered["ok"] is False
    assert "prospective_audit_digest_invalid" in tampered["blockers"]
