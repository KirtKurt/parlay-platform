from copy import deepcopy
from types import SimpleNamespace

import pytest

from hello_world import mlb_historical_round_extension_v1 as extension


def _handler(maximum_rounds=12, *, configured_increment=250, policy_minimum=200):
    handler = SimpleNamespace()
    handler.MAX_OPTIMIZATION_ROUNDS = maximum_rounds
    handler.FRESH_AUDIT_INCREMENT_GAMES = configured_increment
    handler.policy_runtime = SimpleNamespace(
        MIN_UNTOUCHED_AUDIT_GAMES=policy_minimum,
    )
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


def _pending_live_state():
    return {
        "phase": "WAITING_FOR_SETTLED_HORIZON",
        "optimizationRound": 11,
        "eligibleGameCount": 4210,
        "targetSettledGames": 4405,
        "freshAuditExpansionRequired": True,
        "freshAuditStartDate": "2026-08-08",
        "freshAuditCollectedDayCount": 0,
        "freshAuditCollectedGameCount": 0,
        "evaluatedAuditWindows": [
            {"dates": ["2026-07-20", "2026-08-07"]},
        ],
        "latestExperiment": {
            "experimentId": "20260808T051432Z-0450d14d1a",
            "status": "CANDIDATE_REJECTED",
            "promotionGate": {
                "passed": False,
                "settledGameCount": 4155,
            },
        },
    }


def test_rejected_round_reopens_only_with_strictly_later_audit():
    handler = _handler()
    extension.install(handler)

    value = handler._migrate_state(_rejected_state())

    assert handler.FRESH_AUDIT_INCREMENT_GAMES == 200
    assert value["phase"] == "BACKFILLING"
    assert value["optimizationRound"] == 6
    assert value["targetSettledGames"] == 3077
    assert value["freshAuditStartDate"] == "2026-05-01"
    assert value["freshAuditExpansionRequired"] is True
    assert value["lastError"] is None
    recovery = value["optimizationRoundLimitRecovery"]
    assert recovery["activeMaximumOptimizationRounds"] == 12
    assert recovery["latestPreviouslyEvaluatedAuditDate"] == "2026-04-30"
    assert recovery["priorCandidateAuthorityGranted"] is False
    assert recovery["canonicalFreshAuditIncrementGames"] == 200


def test_pending_legacy_target_rebases_to_policy_minimum_without_touching_boundaries():
    handler = _handler()
    extension.install(handler)
    state = _pending_live_state()

    value = handler._migrate_state(state)

    assert handler.FRESH_AUDIT_INCREMENT_GAMES == 200
    assert value["targetSettledGames"] == 4355
    assert value["freshAuditStartDate"] == state["freshAuditStartDate"]
    assert value["evaluatedAuditWindows"] == state["evaluatedAuditWindows"]
    repair = value["canonicalAuditCadenceRepair"]
    assert repair["previousConfiguredIncrementGames"] == 250
    assert repair["policyMinimumUntouchedAuditGames"] == 200
    assert repair["priorTargetSettledGames"] == 4405
    assert repair["newTargetSettledGames"] == 4355
    assert repair["evaluatedAuditWindowsPreserved"] is True
    assert repair["promotionGateWeakened"] is False


def test_pending_target_repair_is_idempotent():
    handler = _handler()
    extension.install(handler)

    first = handler._migrate_state(_pending_live_state())
    second = handler._migrate_state(first)

    assert second == first


@pytest.mark.parametrize(
    "mutation",
    [
        {"champion": {"policyDigest": "active"}},
        {"productionCutover": {"active": True}},
        {"freshAuditCollectedGameCount": 1},
        {"freshAuditCollectedDayCount": 1},
        {"targetSettledGames": 4404},
        {"freshAuditExpansionRequired": False},
        {"freshAuditStartDate": "2026-08-07"},
        {"phase": "TRAINING"},
    ],
)
def test_pending_target_is_not_rebased_when_provenance_or_safety_guard_fails(mutation):
    handler = _handler()
    extension.install(handler)
    state = _pending_live_state()
    state.update(mutation)

    value = handler._migrate_state(state)

    assert value.get("targetSettledGames") == state.get("targetSettledGames")
    assert "canonicalAuditCadenceRepair" not in value


def test_pending_target_is_not_rebased_for_non_rejected_or_passed_candidate():
    for latest in (
        {"status": "CANDIDATE_READY", "promotionGate": {"passed": False, "settledGameCount": 4155}},
        {"status": "CANDIDATE_REJECTED", "promotionGate": {"passed": True, "settledGameCount": 4155}},
        {"status": "CANDIDATE_REJECTED", "promotionGate": {"passed": False}},
    ):
        handler = _handler()
        extension.install(handler)
        state = _pending_live_state()
        state["latestExperiment"] = latest

        value = handler._migrate_state(state)

        assert value["targetSettledGames"] == 4405
        assert "canonicalAuditCadenceRepair" not in value


def test_policy_floor_cannot_be_weakened():
    handler = _handler(configured_increment=199, policy_minimum=200)

    with pytest.raises(RuntimeError, match="below the promotion policy"):
        extension.install(handler)


def test_policy_itself_cannot_drop_below_200():
    handler = _handler(configured_increment=250, policy_minimum=199)

    with pytest.raises(RuntimeError, match="cannot be below 200"):
        extension.install(handler)


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
    assert value["targetSettledGames"] == 4355
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
