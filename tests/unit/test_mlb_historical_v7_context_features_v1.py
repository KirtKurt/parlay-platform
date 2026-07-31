import mlb_historical_v7_context_features_v1 as subject


class FakeLearner:
    FEATURES = ("starterDiff", "bullpenDiff", "lineupDiff")
    FEATURE_VERSION = "base"

    @staticmethod
    def pair_features(home, away, policy):
        return {
            "starterDiff": 2.0,
            "bullpenDiff": -1.0,
            "lineupDiff": 5.0,
        }

    @staticmethod
    def _fundamental(signal, names):
        for source in (
            signal.get("fundamentals"),
            signal.get("fundamentalsSnapshotV2"),
            signal,
        ):
            if not isinstance(source, dict):
                continue
            for name in names:
                if source.get(name) is not None:
                    return float(source[name])
        return None


def _signal(**values):
    return {
        "historicalBbsPriorContextApplied": True,
        "fundamentalsSnapshotV2": values,
    }


def test_prior_history_and_run_environment_become_trainable_features():
    learner = FakeLearner()
    subject.install(learner)
    home = _signal(
        bbsHistoryGames=30,
        bbsHistoryCoverage=1.0,
        bbsWinRate5=0.8,
        bbsWinRate10=0.7,
        bbsWinRate30=0.6,
        bbsRunDiffPerGame5=2.0,
        bbsRunDiffPerGame10=1.0,
        bbsRunsForPerGame10=5.5,
        bbsRunsAgainstPerGame10=4.0,
        bbsStreakNormalized=0.5,
        bbsRestDaysNormalized=0.25,
        bbsVenueWinRate10=0.75,
        parkRunFactor=1.1,
        weatherRunFactor=0.05,
    )
    away = _signal(
        bbsHistoryGames=20,
        bbsHistoryCoverage=0.8,
        bbsWinRate5=0.4,
        bbsWinRate10=0.5,
        bbsWinRate30=0.55,
        bbsRunDiffPerGame5=-1.0,
        bbsRunDiffPerGame10=-0.5,
        bbsRunsForPerGame10=4.0,
        bbsRunsAgainstPerGame10=5.0,
        bbsStreakNormalized=-0.25,
        bbsRestDaysNormalized=0.0,
        bbsVenueWinRate10=0.45,
        parkRunFactor=1.1,
        weatherRunFactor=0.05,
    )

    values = learner.pair_features(home, away, {})

    assert values["bbsPriorAvailable"] == 1.0
    assert values["bbsPriorWinRate10Diff"] == 0.2
    assert values["bbsPriorRunDiff10Diff"] == 0.15
    assert values["bbsPriorHistoryGamesDiff"] == 1.0 / 3.0
    assert values["bbsPriorHistoryCoverageMin"] == 0.8
    assert round(values["parkRunFactorCentered"], 8) == 0.1
    assert values["weatherRunFactor"] == 0.05
    assert round(values["starterRunEnvironmentInteraction"], 8) == 0.3
    assert round(values["bullpenRunEnvironmentInteraction"], 8) == -0.15
    assert round(values["lineupRunEnvironmentInteraction"], 8) == 0.75
    assert subject.EXTRA_FEATURES[-1] in learner.FEATURES


def test_missing_prior_history_is_explicitly_zero_and_install_is_idempotent():
    learner = FakeLearner()
    subject.install(learner)
    first = learner.FEATURES
    subject.install(learner)
    values = learner.pair_features({}, {}, {})
    assert learner.FEATURES == first
    assert values["bbsPriorAvailable"] == 0.0
    assert values["bbsPriorWinRate5Diff"] == 0.0
    assert values["bbsPriorHistoryCoverageMin"] == 0.0
