from scripts import run_mlb_historical_supervised_v9_shadow_v2 as subject


def _result(accepted=True):
    policy = {
        "supervisedEnabled": 1.0,
        "supervisedIntercept": 0.25,
        "supervisedBlend": 0.75,
        "supervisedTemperature": 0.8,
    }
    return {
        "searchVersion": "MLB-HISTORICAL-SUPERVISED-v9.1-test",
        "candidate": {"policy": policy, "policyDigest": "policy-digest"},
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
    }


def test_handoff_reads_nested_candidate_policy_and_creates_stable_model_digest():
    first = subject.candidate_handoff(_result(), "dataset-fingerprint")
    second = subject.candidate_handoff(_result(), "dataset-fingerprint")
    assert first["policy"]["supervisedEnabled"] == 1.0
    assert first["candidateKind"] == "SUPERVISED_V9"
    assert first["modelDigest"] == second["modelDigest"]
    assert first["digest"] == second["digest"]
    assert first["eligibleForCanonicalSeed"] is True
    assert first["promotionAuthority"] is False
    assert first["productionAuthority"] is False
    assert first["frozenBeforeUntouchedHoldout"] is True
    assert first["holdoutLabelsUsedForFitOrSelection"] is False


def test_baseline_fallback_is_durable_but_not_seed_eligible():
    value = subject.candidate_handoff(_result(accepted=False), "dataset-fingerprint")
    assert value["candidateKind"] == "BASELINE_FALLBACK"
    assert value["policy"]
    assert value["modelDigest"]
    assert value["eligibleForCanonicalSeed"] is False
