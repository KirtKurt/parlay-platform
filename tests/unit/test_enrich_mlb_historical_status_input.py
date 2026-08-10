from __future__ import annotations

import hashlib
import json

import pytest

from scripts import build_mlb_historical_status as builder
from scripts import enrich_mlb_historical_status_input as enrichment


def experiment_bytes():
    value = {
        "promotionGate": {
            "passed": False,
            "settledGameCount": 4155,
            "trainingGameCount": 3632,
            "walkForwardGameCount": 267,
            "untouchedHoldoutGameCount": 256,
        },
        "candidate": {
            "policyDigest": "candidate-digest",
            "walkForward": {
                "gameCount": 267,
                "dayCount": 20,
                "overallAccuracy": 0.5505618,
                "meanDailyAccuracy": 0.551262974,
                "minimumDailyAccuracy": 0.0,
                "minimumSlateCoverage": 1.0,
                "brierScore": 0.2471,
                "logLoss": 0.6862,
                "policyDigest": "candidate-digest",
            },
            "untouchedHoldout": {
                "gameCount": 256,
                "dayCount": 19,
                "overallAccuracy": 0.5703125,
                "meanDailyAccuracy": 0.571514864,
                "minimumDailyAccuracy": 0.375,
                "minimumSlateCoverage": 1.0,
                "brierScore": 0.2448,
                "logLoss": 0.6814,
                "policyDigest": "candidate-digest",
            },
        },
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def status_response(body: bytes):
    return {
        "ok": True,
        "championValidation": {
            "ok": False,
            "errors": ["no_active_champion"],
        },
        "state": {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-10",
            "currentSlotIndex": 0,
            "endDate": "2026-08-09",
            "eligibleGameCount": 4185,
            "completeSlateCount": 337,
            "targetSettledGames": 4405,
            "optimizationRound": 11,
            "updatedAtUtc": "2026-08-10T09:16:44+00:00",
            "lastError": None,
            "featureRematerializationComplete": True,
            "featureRematerializedSlateCount": 337,
            "featureRematerializationTotalSlateCount": 337,
            "featureRematerializationErrors": [],
            "settledHorizonWait": {
                "blockingError": False,
                "settledHorizonDate": "2026-08-09",
                "nextEligibleSlateDate": "2026-08-10",
            },
            "latestExperiment": {
                "experimentId": "experiment-11",
                "candidatePolicyDigest": "candidate-digest",
                "status": "CANDIDATE_REJECTED",
                "artifact": {
                    "bucket": "bucket",
                    "key": "experiment.json",
                    "versionId": "version-1",
                    "sha256": hashlib.sha256(body).hexdigest(),
                },
                "promotionGate": {
                    "passed": False,
                    "settledGameCount": 4155,
                    "trainingGameCount": 3632,
                    "walkForwardGameCount": 267,
                    "walkForwardDayCount": 20,
                    "walkForwardMeanDailyAccuracy": 0.551262974,
                    "walkForwardMinimumDailyAccuracy": 0.0,
                    "untouchedHoldoutGameCount": 256,
                    "untouchedHoldoutDayCount": 19,
                    "untouchedHoldoutMeanDailyAccuracy": 0.571514864,
                    "untouchedHoldoutMinimumDailyAccuracy": 0.375,
                    "errors": ["accuracy_gate_failed"],
                    "overfitChecks": {
                        "brierDeltaVsBaseline": 0,
                        "logLossDeltaVsBaseline": 0,
                    },
                },
            },
        },
    }


def test_enrichment_publishes_absolute_candidate_metrics():
    body = experiment_bytes()
    enriched = enrichment.enrich(status_response(body), body)
    latest = enriched["state"]["latestExperiment"]

    assert latest["walkForwardMetrics"]["brierScore"] == 0.2471
    assert latest["walkForwardMetrics"]["logLoss"] == 0.6862
    assert latest["untouchedHoldoutMetrics"]["brierScore"] == 0.2448
    assert latest["untouchedHoldoutMetrics"]["logLoss"] == 0.6814
    source = latest["absoluteCalibrationMetricsSource"]
    assert source["verifiedSha256"] == hashlib.sha256(body).hexdigest()
    assert source["productionAuthorityChanged"] is False

    summary = builder.build_summary(enriched)
    metrics = summary["latestChallengerMetrics"]
    assert metrics["absoluteScoresPublished"] is True
    assert metrics["walkForwardBrierScore"] == 0.2471
    assert metrics["walkForwardLogLoss"] == 0.6862
    assert metrics["untouchedHoldoutBrierScore"] == 0.2448
    assert metrics["untouchedHoldoutLogLoss"] == 0.6814
    assert (
        summary["latestAccuracy"]["absoluteCalibrationScoresPublished"]
        is True
    )


def test_enrichment_fails_closed_on_artifact_hash_mismatch():
    body = experiment_bytes()
    status = status_response(body)
    status["state"]["latestExperiment"]["artifact"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="sha256 mismatch"):
        enrichment.enrich(status, body)


def test_enrichment_fails_closed_on_gate_mismatch():
    body = experiment_bytes()
    status = status_response(body)
    status["state"]["latestExperiment"]["promotionGate"][
        "trainingGameCount"
    ] = 999

    with pytest.raises(ValueError, match="gate mismatch:trainingGameCount"):
        enrichment.enrich(status, body)


def test_enrichment_fails_closed_on_candidate_digest_mismatch():
    body = experiment_bytes()
    status = status_response(body)
    status["state"]["latestExperiment"][
        "candidatePolicyDigest"
    ] = "wrong"

    with pytest.raises(ValueError, match="candidate digest mismatch"):
        enrichment.enrich(status, body)
