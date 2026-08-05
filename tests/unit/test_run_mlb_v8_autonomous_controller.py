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


def test_healthy_training_is_fully_autonomous_without_promotion():
    result = controller.decide(
        training=_training(),
        context={
            "ok": True,
            "provider": "official_mlb_plus_internal_canonical_context",
            "bbsApiUsed": False,
            "productionAuthorityChanged": False,
        },
    )

    assert result["ok"] is True
    assert result["fullyAutonomous"] is True
    assert result["normalOperationManualInterventionRequired"] is False
    assert result["nextAction"] == "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"
    assert result["promotionRequested"] is False


def test_context_failure_schedules_retry_without_stalling_learning():
    result = controller.decide(
        training=_training(),
        context={"ok": False, "bbsApiUsed": False},
    )

    assert result["ok"] is True
    assert "context_backfill_retry_required" in result["blockers"]
    assert result["contextBackfillRetryScheduled"] is True
    assert result["nextAction"] == (
        "RETRY_CONTEXT_BACKFILL_AND_CONTINUE_TRAINING"
    )


def test_promotion_decision_requires_verification_and_rollback():
    result = controller.decide(
        training=_training(decision="AUTO_PROMOTE_GUARDED_CHAMPION"),
        context={"ok": True, "bbsApiUsed": False},
    )

    assert result["promotionRequested"] is True
    assert result["verificationRequiredAfterPromotion"] is True
    assert result["rollbackRequiredOnVerificationFailure"] is True


def test_invalid_training_digest_fails_closed():
    training = _training()
    training["resultDigest"] = "tampered"
    result = controller.decide(training=training, context={"ok": True})

    assert result["ok"] is False
    assert "training_result_digest_invalid" in result["blockers"]
    assert result["nextAction"] == "REPAIR_AND_RETRY_AUTONOMOUS_TRAINING"
