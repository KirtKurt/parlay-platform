from types import SimpleNamespace

from hello_world import mlb_v8_autonomy_v1 as autonomy


def _report(*, selected="market_baseline", gate=False, prospective=True, production=False):
    return {
        "ok": True,
        "selection": {
            "selectedFeatureGroup": selected,
            "selectedL2": 0.02,
            "candidateCount": 24,
            "foldCount": 3,
            "selectionUsedUntouchedAudit": False,
            "selectionGuard": {
                "learnedEligibleCandidateCount": 0,
                "thresholds": {
                    "regularizationGrid": [0.01, 0.02],
                },
            },
            "ablation": {
                "market_baseline": {
                    "l2": 0.01,
                    "oofMetrics": {"overallAccuracy": 0.55},
                    "guard": {"eligible": True, "errors": []},
                },
                "market_temporal_team": {
                    "l2": 0.02,
                    "oofMetrics": {
                        "overallAccuracy": 0.56,
                        "meanDailyAccuracy": 0.55,
                        "minimumDailyAccuracy": 0.20,
                    },
                    "guard": {
                        "eligible": False,
                        "errors": ["positive_fold_count_below_floor"],
                        "stability": {
                            "overallAccuracyUplift": 0.01,
                            "meanDailyAccuracyUplift": 0.0,
                            "positiveFoldCount": 1,
                        },
                    },
                },
            },
        },
        "model": {
            "featureGroup": selected,
            "trainingSteps": 0 if selected == "market_baseline" else 700,
        },
        "promotionGate": {"passed": gate},
        "freshProspectiveAuditRequired": prospective,
        "productionPromotionEligible": production,
        "resultDigest": "old",
    }


def test_baseline_retention_reports_nonzero_learning_execution():
    value = autonomy.decorate_result(_report())

    proof = value["learningExecution"]
    assert proof["learningExecuted"] is True
    assert proof["learnedCandidateCount"] == 22
    assert proof["learnedCandidateFoldFitCount"] == 66
    assert proof["crossValidationOptimizationSteps"] == 14520
    assert proof["totalOptimizationSteps"] == 14520
    assert proof["learnedCandidateSelected"] is False
    assert proof["marketBaselineRetainedByGuard"] is True
    assert proof["qualityGateWeakened"] is False
    assert value["learningStatus"] == "LEARNING_EXECUTED_MARKET_BASELINE_RETAINED"
    assert value["autonomyDecision"] == "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"
    assert value["resultDigest"] != "old"


def test_learned_candidate_advances_only_through_remaining_gates():
    shadow = autonomy.decorate_result(
        _report(selected="market_temporal_team", gate=False)
    )
    prospective = autonomy.decorate_result(
        _report(selected="market_temporal_team", gate=True, prospective=True)
    )
    promote = autonomy.decorate_result(
        _report(
            selected="market_temporal_team",
            gate=True,
            prospective=False,
            production=True,
        )
    )

    assert shadow["learningStatus"] == "LEARNED_CANDIDATE_SELECTED"
    assert shadow["autonomyDecision"] == "CONTINUE_AUTONOMOUS_SHADOW_VALIDATION"
    assert prospective["autonomyDecision"] == "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT"
    assert promote["autonomyDecision"] == "AUTO_PROMOTE_GUARDED_CHAMPION"
    assert promote["autonomy"]["automaticWagerAllowed"] is False


def test_install_wraps_trainer_once():
    calls = []

    def train(*_args, **_kwargs):
        calls.append(1)
        return _report()

    module = SimpleNamespace(train_and_evaluate=train)
    autonomy.install(module)
    wrapped = module.train_and_evaluate
    autonomy.install(module)

    value = module.train_and_evaluate([])
    assert module.train_and_evaluate is wrapped
    assert calls == [1]
    assert value["learningExecution"]["learningExecuted"] is True
