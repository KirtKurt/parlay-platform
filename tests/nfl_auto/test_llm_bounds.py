from nfl_auto.llm_analyst import BASELINE_TRIALS, validated_trials


def test_llm_trials_are_strictly_allowlisted() -> None:
    result = validated_trials(
        {
            "trials": [
                {"learning_rate": 0.03, "l2": 0.002, "epochs": 90},
                {"learning_rate": 9, "l2": 0, "epochs": 9999},
                {"learning_rate": 0.02, "l2": 0.001, "epochs": 80, "publish": True},
                {"drop_audit": True},
            ]
        }
    )
    assert result == [
        {"learning_rate": 0.03, "l2": 0.002, "epochs": 90},
        {"learning_rate": 0.02, "l2": 0.001, "epochs": 80},
    ]
    assert all(set(trial) == {"learning_rate", "l2", "epochs"} for trial in result)
    assert len(BASELINE_TRIALS) >= 3
