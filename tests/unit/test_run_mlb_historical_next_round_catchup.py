from __future__ import annotations

import copy

import pytest

from scripts import run_mlb_historical_next_round_catchup as catchup
from scripts import run_mlb_historical_watchdog as watchdog


CEILING = "2026-12-31"


def state(*, phase=watchdog.WAITING_PHASE, eligible=4185, target=4405):
    value = {
        "phase": phase,
        "endDate": "2026-08-09",
        "currentDate": "2026-08-10",
        "currentSlotIndex": 0,
        "eligibleGameCount": eligible,
        "completeSlateCount": 337,
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": 337,
        "featureRematerializationTotalSlateCount": 337,
        "featureRematerializationErrors": [],
        "targetSettledGames": target,
        "nextOptimizationReady": eligible >= target,
        "optimizationRound": 11,
        "optimizationCompletedAtUtc": "2026-08-08T05:14:32+00:00",
        "networkRequestCount": 26718,
        "creditsConsumed": 267180,
        "revision": 6045,
        "lastError": None,
        "lastQuota": {"x-requests-remaining": 4915998},
        "freshAuditExpansionRequired": True,
        "freshAuditCollectedDayCount": 0,
        "freshAuditCollectedGameCount": 0,
        "latestExperiment": {
            "experimentId": "round-11",
            "status": "CANDIDATE_REJECTED",
            "promotionGate": {
                "passed": False,
                "trainingGameCount": 3632,
                "walkForwardMeanDailyAccuracy": 0.55,
                "untouchedHoldoutMeanDailyAccuracy": 0.57,
                "overfitChecks": {
                    "brierDeltaVsBaseline": 0,
                    "logLossDeltaVsBaseline": 0,
                },
            },
        },
    }
    if phase == watchdog.WAITING_PHASE:
        value["rangeExtensionNextRetryDate"] = "2026-08-10"
        value["settledHorizonWait"] = {
            "version": watchdog.WAITING_CONTRACT_VERSION,
            "authorizedThroughDate": "2026-08-09",
            "settledHorizonDate": "2026-08-09",
            "configuredCeilingDate": CEILING,
            "nextEligibleSlateDate": "2026-08-10",
            "blockingError": False,
        }
    return value


def test_healthy_wait_below_target_is_not_a_failure():
    assert (
        catchup.classify_state(
            state(), expected_ceiling=CEILING
        )
        == catchup.WAITING_FOR_EVIDENCE
    )


def test_ready_state_requires_next_optimization():
    ready = state(eligible=4405, target=4405)

    assert (
        catchup.classify_state(
            ready, expected_ceiling=CEILING
        )
        == catchup.RUN_NEXT_OPTIMIZATION
    )


def test_active_pipeline_is_advanced_but_not_misclassified_as_ready():
    active = state(phase="BACKFILLING")
    active["currentDate"] = "2026-08-09"

    assert (
        catchup.classify_state(
            active, expected_ceiling=CEILING
        )
        == catchup.ADVANCE_ACTIVE_PIPELINE
    )


def test_rejected_candidate_below_target_waits_for_more_evidence():
    rejected = state(phase="CANDIDATE_REJECTED")

    assert (
        catchup.classify_state(
            rejected, expected_ceiling=CEILING
        )
        == catchup.WAITING_FOR_EVIDENCE
    )


def test_false_data_range_exhaustion_before_ceiling_fails_closed():
    exhausted = state(phase="DATA_RANGE_EXHAUSTED")

    with pytest.raises(
        ValueError,
        match="data_range_exhausted_before_configured_ceiling",
    ):
        catchup.classify_state(
            exhausted, expected_ceiling=CEILING
        )


def test_rematerialization_must_cover_current_completed_slate_count():
    broken = state()
    broken["featureRematerializedSlateCount"] = 335
    broken["featureRematerializationTotalSlateCount"] = 335

    with pytest.raises(
        ValueError,
        match="feature_rematerialization_does_not_cover_completed_slates",
    ):
        catchup.classify_state(
            broken, expected_ceiling=CEILING
        )


def test_experiment_advance_requires_concrete_identity_or_round_change():
    baseline = state()
    unchanged = copy.deepcopy(baseline)
    assert catchup._experiment_advanced(baseline, unchanged) is False

    changed = copy.deepcopy(baseline)
    changed["latestExperiment"]["experimentId"] = "round-12"
    assert catchup._experiment_advanced(baseline, changed) is True

    round_changed = copy.deepcopy(baseline)
    round_changed["optimizationRound"] = 12
    assert catchup._experiment_advanced(baseline, round_changed) is True


def test_summary_exposes_readiness_and_model_metrics():
    summary = catchup.summarize_state(state())

    assert summary["gamesUntilNextOptimization"] == 220
    assert summary["nextOptimizationReady"] is False
    assert summary["trainingGameCount"] == 3632
    assert summary["walkForwardMeanDailyAccuracy"] == 0.55
    assert summary["untouchedHoldoutMeanDailyAccuracy"] == 0.57
    assert summary["brierDeltaVsBaseline"] == 0
    assert summary["logLossDeltaVsBaseline"] == 0
