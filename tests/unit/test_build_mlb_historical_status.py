from __future__ import annotations

from datetime import datetime, timezone

from scripts import build_mlb_historical_status as builder


def status_response():
    return {
        "ok": True,
        "championValidation": {
            "ok": False,
            "errors": ["no_active_champion"],
        },
        "cutoverValidation": {
            "ok": False,
            "errors": ["not_cut_over_before_first_promotion"],
        },
        "productionAuthority": {
            "historicalChampionOnly": False,
        },
        "state": {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-10",
            "currentSlotIndex": 0,
            "endDate": "2026-08-09",
            "eligibleGameCount": 4185,
            "completeSlateCount": 337,
            "targetSettledGames": 4405,
            "nextOptimizationReady": False,
            "optimizationRound": 11,
            "optimizationCompletedAtUtc": "2026-08-08T05:14:32+00:00",
            "updatedAtUtc": "2026-08-10T09:16:44+00:00",
            "revision": 6045,
            "networkRequestCount": 26718,
            "creditsConsumed": 267180,
            "lastError": None,
            "lastErrorAtUtc": None,
            "lastQuota": {"x-requests-remaining": 4915998},
            "featureDatasetVersion": "feature-v9",
            "featureRematerializationComplete": True,
            "featureRematerializedSlateCount": 337,
            "featureRematerializationTotalSlateCount": 337,
            "featureRematerializationErrors": [],
            "freshAuditExpansionRequired": True,
            "freshAuditStartDate": "2026-08-08",
            "freshAuditCollectedDayCount": 0,
            "freshAuditCollectedGameCount": 0,
            "completedSlates": [
                {
                    "slateDateEt": "2026-08-08",
                    "eligibleGameCount": 15,
                },
                {
                    "slateDateEt": "2026-08-09",
                    "eligibleGameCount": 15,
                },
            ],
            "rangeExtensionNextRetryDate": "2026-08-10",
            "settledHorizonWait": {
                "version": (
                    "MLB-HISTORICAL-STATE-INTEGRITY-v2-"
                    "settled-horizon-ledger-aware"
                ),
                "authorizedThroughDate": "2026-08-09",
                "settledHorizonDate": "2026-08-09",
                "configuredCeilingDate": "2026-12-31",
                "nextEligibleSlateDate": "2026-08-10",
                "blockingError": False,
            },
            "latestExperiment": {
                "experimentId": "experiment-11",
                "status": "CANDIDATE_REJECTED",
                "promotionGate": {
                    "passed": False,
                    "errors": [
                        "candidate_did_not_improve_walk_forward_daily_objective"
                    ],
                    "trainingGameCount": 3632,
                    "settledGameCount": 4155,
                    "walkForwardGameCount": 267,
                    "walkForwardDayCount": 20,
                    "walkForwardMeanDailyAccuracy": 0.551262974,
                    "walkForwardMinimumDailyAccuracy": 0.0,
                    "untouchedHoldoutGameCount": 256,
                    "untouchedHoldoutDayCount": 19,
                    "untouchedHoldoutMeanDailyAccuracy": 0.571514864,
                    "untouchedHoldoutMinimumDailyAccuracy": 0.375,
                    "overfitChecks": {
                        "brierDeltaVsBaseline": 0.0,
                        "logLossDeltaVsBaseline": 0.0,
                    },
                },
            },
        },
    }


def function_configuration():
    return {
        "Handler": (
            "mlb_historical_optimizer_v7_recovery_entrypoint."
            "lambda_handler"
        ),
        "Environment": {
            "Variables": {
                "MLB_HISTORICAL_END_DATE": "2026-12-31",
                "INQSI_DEPLOY_GIT_SHA": "6d718a9",
            }
        },
    }


def test_waiting_status_is_explicit_and_evidence_honest():
    summary = builder.build_summary(
        status_response(),
        function_configuration=function_configuration(),
        checked_at=datetime(
            2026, 8, 10, 16, 7, 15, tzinfo=timezone.utc
        ),
    )

    assert summary["ok"] is True
    assert summary["waitingHealthy"] is True
    assert summary["stalledStage"] == "SETTLED_CORPUS_ACCUMULATION"
    assert summary["authorizedThroughDate"] == "2026-08-09"
    assert summary["configuredCeilingDate"] == "2026-12-31"
    assert summary["safeSettledHorizonDate"] == "2026-08-09"
    assert summary["nextEligibleSlateDate"] == "2026-08-10"
    assert summary["deploymentGitSha"] == "6d718a9"
    assert summary["gamesUntilNextOptimization"] == 220
    assert summary["currentTrainingGameCount"] == 3632
    assert summary["runtimeBlockers"] == []
    assert summary["modelPromotionBlockers"] == [
        "candidate_did_not_improve_walk_forward_daily_objective"
    ]
    assert summary["championStatus"] == "NO_ACTIVE_CHAMPION"
    assert summary["latestChallengerMetrics"]["corpusCurrent"] is False
    assert (
        summary["latestChallengerMetrics"][
            "absoluteScoresPublished"
        ]
        is False
    )
    assert summary["latestAccuracy"]["walkForwardBrierScore"] is None
    assert summary["latestAccuracy"]["brierDeltaVsBaseline"] == 0.0
    assert summary["provisionalFreshAuditCollectedGameCount"] == 30
    assert summary["sourceStateStaleButHealthyWait"] is True


def test_absolute_scores_are_published_when_experiment_contains_them():
    response = status_response()
    latest = response["state"]["latestExperiment"]
    latest["walkForwardMetrics"] = {
        "brierScore": 0.24,
        "logLoss": 0.69,
    }
    latest["untouchedHoldoutMetrics"] = {
        "brierScore": 0.23,
        "logLoss": 0.67,
    }

    summary = builder.build_summary(
        response,
        function_configuration=function_configuration(),
        checked_at=datetime(
            2026, 8, 10, 10, 0, tzinfo=timezone.utc
        ),
    )

    metrics = summary["latestChallengerMetrics"]
    assert metrics["absoluteScoresPublished"] is True
    assert metrics["walkForwardBrierScore"] == 0.24
    assert metrics["walkForwardLogLoss"] == 0.69
    assert metrics["untouchedHoldoutBrierScore"] == 0.23
    assert metrics["untouchedHoldoutLogLoss"] == 0.67


def test_rematerialization_mismatch_is_a_runtime_blocker():
    response = status_response()
    response["state"]["featureRematerializedSlateCount"] = 335
    response["state"]["featureRematerializationTotalSlateCount"] = 335

    summary = builder.build_summary(
        response,
        function_configuration=function_configuration(),
        checked_at=datetime(
            2026, 8, 10, 10, 0, tzinfo=timezone.utc
        ),
    )

    assert summary["ok"] is False
    assert summary["stalledStage"] == "RUNTIME_BLOCKED"
    assert (
        "feature_rematerialization_does_not_cover_completed_slates"
        in summary["runtimeBlockers"]
    )


def test_blocking_wait_is_not_reported_as_healthy():
    response = status_response()
    response["state"]["settledHorizonWait"]["blockingError"] = True

    summary = builder.build_summary(
        response,
        function_configuration=function_configuration(),
        checked_at=datetime(
            2026, 8, 10, 10, 0, tzinfo=timezone.utc
        ),
    )

    assert summary["waitingHealthy"] is False
    assert summary["stalledStage"] == "RUNTIME_BLOCKED"
    assert "settled_horizon_wait_is_blocking" in summary["runtimeBlockers"]
