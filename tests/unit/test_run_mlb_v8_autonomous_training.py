import json

import run_mlb_v8_autonomous_training as entrypoint


def _base_result():
    return {
        "ok": True,
        "selection": {
            "selectedFeatureGroup": "market_baseline",
            "candidateCount": 6,
            "foldCount": 3,
            "selectionUsedUntouchedAudit": False,
            "selectionGuard": {
                "learnedEligibleCandidateCount": 0,
                "thresholds": {"regularizationGrid": [0.02, 0.20]},
            },
            "ablation": {
                "market_baseline": {"guard": {"eligible": True}},
                "market_temporal_team": {
                    "l2": 0.02,
                    "oofMetrics": {"overallAccuracy": 0.55},
                    "guard": {
                        "eligible": False,
                        "errors": ["aggregate_accuracy_uplift_below_floor"],
                        "stability": {
                            "overallAccuracyUplift": 0.0,
                            "meanDailyAccuracyUplift": 0.0,
                            "positiveFoldCount": 0,
                        },
                    },
                },
            },
        },
        "model": {"featureGroup": "market_baseline", "trainingSteps": 0},
        "promotionGate": {"passed": False},
        "freshProspectiveAuditRequired": True,
        "productionPromotionEligible": False,
        "recordCountLoaded": 1500,
    }


def test_run_decorates_and_rewrites_training_report(monkeypatch, tmp_path):
    output = tmp_path / "training.json"
    calls = {}

    def fake_run(**kwargs):
        calls.update(kwargs)
        output.write_text(json.dumps(_base_result()))
        return _base_result()

    monkeypatch.setattr(entrypoint.runner, "run", fake_run)

    value = entrypoint.run(
        region="us-east-1",
        stack_name="historical",
        table_name="snapshots",
        output=output,
    )

    stored = json.loads(output.read_text())
    assert calls["region"] == "us-east-1"
    assert value["learningExecution"]["learningExecuted"] is True
    assert value["learningExecution"]["totalOptimizationSteps"] > 0
    assert value["learningStatus"] == (
        "LEARNING_EXECUTED_MARKET_BASELINE_RETAINED"
    )
    assert stored["resultDigest"] == value["resultDigest"]
    assert stored["historicalBbsRequired"] is False
    assert stored["providerNeutralTrainingAllowed"] is True
