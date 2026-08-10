from types import SimpleNamespace

from hello_world import mlb_historical_round_extension_v1 as extension


def _handler(maximum_rounds=12):
    handler = SimpleNamespace()
    handler.MAX_OPTIMIZATION_ROUNDS = maximum_rounds
    handler.FRESH_AUDIT_INCREMENT_GAMES = 250
    handler.END_DATE = "2026-07-24"
    handler._now_iso = lambda: "2026-07-26T18:00:00+00:00"
    handler._migrate_state = lambda state: dict(state)
    return handler


def _rejected_state():
    return {
        "phase": "CANDIDATE_REJECTED",
        "optimizationRound": 6,
        "paidBackfillAuthorized": True,
        "featureRematerializationComplete": True,
        "featureRematerializationErrors": [],
        "currentDate": "2026-05-01",
        "endDate": "2026-07-24",
        "eligibleGameCount": 2877,
        "targetSettledGames": 2877,
        "freshAuditStartDate": "2026-04-12",
        "evaluatedAuditWindows": [
            {"dates": ["2026-04-12", "2026-04-30"]},
        ],
        "latestExperiment": {
            "status": "CANDIDATE_REJECTED",
            "promotionGate": {"passed": False},
        },
        "lastError": "80% gate not achieved",
    }


def test_rejected_round_reopens_only_with_strictly_later_audit():
    handler = _handler()
    extension.install(handler)

    value = handler._migrate_state(_rejected_state())

    assert value["phase"] == "BACKFILLING"
    assert value["optimizationRound"] == 6
    assert value["targetSettledGames"] == 3127
    assert value["freshAuditStartDate"] == "2026-05-01"
    assert value["freshAuditExpansionRequired"] is True
    assert value["lastError"] is None
    recovery = value["optimizationRoundLimitRecovery"]
    assert recovery["activeMaximumOptimizationRounds"] == 12
    assert recovery["latestPreviouslyEvaluatedAuditDate"] == "2026-04-30"
    assert recovery["priorCandidateAuthorityGranted"] is False


def test_round_does_not_reopen_when_new_ceiling_is_exhausted():
    handler = _handler(maximum_rounds=6)
    extension.install(handler)

    value = handler._migrate_state(_rejected_state())

    assert value["phase"] == "CANDIDATE_REJECTED"
    assert "optimizationRoundLimitRecovery" not in value


def test_round_does_not_reuse_or_overlap_previous_audit_dates():
    handler = _handler()
    extension.install(handler)
    state = _rejected_state()
    state["currentDate"] = "2026-04-30"

    value = handler._migrate_state(state)

    assert value["phase"] == "CANDIDATE_REJECTED"
    assert value["freshAuditStartDate"] == "2026-05-01"
    assert value["freshAuditExpansionRequired"] is True


def test_round_does_not_reopen_after_promotion_or_with_bad_features():
    for mutation in (
        {"champion": {"policyDigest": "active"}},
        {"productionCutover": {"active": True}},
        {"featureRematerializationComplete": False},
        {"featureRematerializationErrors": ["bad"]},
    ):
        handler = _handler()
        extension.install(handler)
        state = _rejected_state()
        state.update(mutation)
        value = handler._migrate_state(state)
        assert value["phase"] == "CANDIDATE_REJECTED"


def test_migrated_backfill_state_repairs_missing_fresh_audit_flags():
    handler = _handler()
    extension.install(handler)
    state = {
        "phase": "BACKFILLING",
        "optimizationRound": 11,
        "paidBackfillAuthorized": True,
        "featureRematerializationComplete": True,
        "featureRematerializationErrors": [],
        "currentDate": "2026-08-08",
        "endDate": "2026-08-08",
        "eligibleGameCount": 4155,
        "targetSettledGames": 4155,
        "freshAuditExpansionRequired": False,
        "freshAuditStartDate": None,
        "evaluatedAuditWindows": [
            {"dates": ["2026-07-20", "2026-08-07"]},
        ],
        "lastError": (
            "OrchestrationError: untouched audit dates were reused after label evaluation: "
            "2026-07-20,2026-08-07"
        ),
    }

    value = handler._migrate_state(state)

    assert value["phase"] == "BACKFILLING"
    assert value["freshAuditExpansionRequired"] is True
    assert value["freshAuditStartDate"] == "2026-08-08"
    assert value["targetSettledGames"] == 4405
    assert value["lastError"] is None
    assert value["auditReuseRecovery"]["latestPreviouslyEvaluatedAuditDate"] == "2026-08-07"
    assert value["auditReuseRecovery"]["strictlyLaterAuditRequired"] is True


def test_optimizing_retry_is_forced_onto_strictly_later_holdout():
    handler = _handler()
    extension.install(handler)
    state = {
        "phase": "OPTIMIZING",
        "optimizationRound": 11,
        "currentDate": "2026-08-08",
        "eligibleGameCount": 4155,
        "targetSettledGames": 4405,
        "freshAuditExpansionRequired": False,
        "freshAuditStartDate": "2026-07-20",
        "evaluatedAuditWindows": [
            {"dates": ["2026-07-20", "2026-08-07"]},
        ],
    }

    value = handler._migrate_state(state)

    assert value["phase"] == "OPTIMIZING"
    assert value["freshAuditExpansionRequired"] is True
    assert value["freshAuditStartDate"] == "2026-08-08"
    assert value["freshAuditCollectedDayCount"] == 0
    assert value["freshAuditCollectedGameCount"] == 0


def test_valid_existing_strictly_later_boundary_is_idempotent():
    handler = _handler()
    extension.install(handler)
    state = {
        "phase": "BACKFILLING",
        "optimizationRound": 11,
        "currentDate": "2026-08-08",
        "eligibleGameCount": 4155,
        "targetSettledGames": 4405,
        "freshAuditExpansionRequired": True,
        "freshAuditStartDate": "2026-08-08",
        "evaluatedAuditWindows": [
            {"dates": ["2026-07-20", "2026-08-07"]},
        ],
    }

    value = handler._migrate_state(state)

    assert value == state
