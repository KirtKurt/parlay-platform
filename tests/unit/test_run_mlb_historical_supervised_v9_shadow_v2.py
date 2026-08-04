from scripts import run_mlb_historical_supervised_v9_shadow_v2 as subject


def _metrics(games, mean_daily, brier, log_loss):
    return {
        "gameCount": games,
        "dayCount": 20,
        "overallAccuracy": mean_daily + 0.01,
        "meanDailyAccuracy": mean_daily,
        "minimumDailyAccuracy": 0.4,
        "brierScore": brier,
        "logLoss": log_loss,
        "exactSlateCoverage": 1.0,
        "dailyPassRate": 0.0,
        "daily": [{"date": "2026-07-01", "accuracy": mean_daily}],
    }


def _result(accepted=True):
    policy = {
        "supervisedEnabled": 1.0,
        "supervisedIntercept": 0.25,
        "supervisedBlend": 0.75,
        "supervisedTemperature": 0.8,
    }
    return {
        "searchVersion": "MLB-HISTORICAL-SUPERVISED-v9.1-test",
        "candidate": {
            "policy": policy,
            "policyDigest": "policy-digest",
            "walkForward": _metrics(259, 0.61, 0.23, 0.65),
            "untouchedHoldout": _metrics(252, 0.60, 0.24, 0.67),
        },
        "baseline": {
            "walkForward": _metrics(259, 0.59, 0.25, 0.70),
            "untouchedHoldout": _metrics(252, 0.58, 0.26, 0.72),
        },
        "supervisedDiagnostics": {
            "outerWalkForwardAccepted": accepted,
            "holdoutEvaluatedAfterFreeze": True,
            "holdoutLabelsUsedForFitOrSelection": False,
            "selectedL2": 0.1,
            "selectedBlend": 0.75,
            "selectedTemperature": 0.8,
            "featureVersion": "features-v2",
            "featureCount": 38,
        },
        "promotionGate": {
            "passed": False,
            "trainingGameCount": 3388,
            "walkForwardGameCount": 259,
            "untouchedHoldoutGameCount": 252,
        },
        "chronologicalPartitions": {
            "training": ["2026-04-01", "2026-06-30"],
            "walkForward": ["2026-07-01", "2026-07-19"],
            "untouchedHoldout": ["2026-07-20", "2026-08-03"],
        },
    }


def test_handoff_reads_nested_candidate_policy_and_creates_stable_model_digest():
    first = subject.candidate_handoff(_result(), "dataset-fingerprint")
    second = subject.candidate_handoff(_result(), "dataset-fingerprint")
    assert first["policy"]["supervisedEnabled"] == 1.0
    assert first["candidateKind"] == "SUPERVISED_V9"
    assert first["modelDigest"] == second["modelDigest"]
    assert first["metricPartitionFingerprint"] == second["metricPartitionFingerprint"]
    assert first["digest"] == second["digest"]
    assert first["eligibleForCanonicalSeed"] is True
    assert first["promotionAuthority"] is False
    assert first["productionAuthority"] is False
    assert first["frozenBeforeUntouchedHoldout"] is True
    assert first["holdoutLabelsUsedForFitOrSelection"] is False
    assert first["metricsCurrentPartition"] is True
    assert first["selectedWalkForwardMetrics"]["brierScore"] == 0.23
    assert first["selectedUntouchedHoldoutMetrics"]["logLoss"] == 0.67
    assert first["supervisedUntouchedHoldoutMetrics"]["gameCount"] == 252


def test_baseline_fallback_is_durable_but_not_seed_eligible_and_hides_rejected_holdout():
    value = subject.candidate_handoff(_result(accepted=False), "dataset-fingerprint")
    assert value["candidateKind"] == "BASELINE_FALLBACK"
    assert value["policy"]
    assert value["modelDigest"]
    assert value["eligibleForCanonicalSeed"] is False
    assert value["metricsCurrentPartition"] is True
    assert value["selectedWalkForwardMetrics"]["brierScore"] == 0.25
    assert value["selectedUntouchedHoldoutMetrics"]["logLoss"] == 0.72
    assert value["supervisedWalkForwardMetrics"]["brierScore"] == 0.23
    assert value["supervisedUntouchedHoldoutMetrics"] is None


def test_metric_count_mismatch_fails_closed():
    result = _result(accepted=True)
    result["candidate"]["untouchedHoldout"]["gameCount"] = 251

    value = subject.candidate_handoff(result, "dataset-fingerprint")

    assert value["metricsCurrentPartition"] is False
    assert value["eligibleForCanonicalSeed"] is False
    assert any(
        item.startswith("untouched_holdout_game_count_mismatch")
        for item in value["metricPublicationErrors"]
    )


def test_report_integrity_publishes_selected_current_partition_metrics(tmp_path):
    handoff = subject.candidate_handoff(_result(accepted=False), "dataset-fingerprint")
    report_path = tmp_path / "report.json"
    report_path.write_text(
        __import__("json").dumps({
            "ok": True,
            "shadowRefitPerformed": True,
            "blockers": [],
            "supervisedCandidate": {
                "diagnostics": {
                    "strictBinaryLabels": True,
                    "v8ExpansionFallbackEnabled": True,
                    "holdoutEvaluatedAfterFreeze": True,
                    "holdoutLabelsUsedForFitOrSelection": False,
                }
            },
            "canonicalCandidateHandoff": handoff,
        })
    )

    ok, report = subject._enforce_report_integrity(str(report_path))

    assert ok is True
    assert report["latestMetricEvidence"]["metricsCurrentPartition"] is True
    candidate = report["supervisedCandidate"]
    assert candidate["brierScore"] == 0.25
    assert candidate["logLoss"] == 0.70
    assert candidate["untouchedHoldoutBrierScore"] == 0.26
    assert candidate["untouchedHoldoutLogLoss"] == 0.72
    assert report["zeroBytePublicationAllowed"] is False
