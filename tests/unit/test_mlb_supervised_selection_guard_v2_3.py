from types import SimpleNamespace

from hello_world import mlb_supervised_daily_objective_v2_1 as objective
from hello_world import mlb_supervised_selection_guard_v2_3 as guard


def _metrics(acc, correct, n=100, mean=None, brier=0.24, logloss=0.68, ece=0.04):
    return {
        "gameCount": n,
        "correct": correct,
        "overallAccuracy": acc,
        "meanDailyAccuracy": acc if mean is None else mean,
        "dailyPassRate": 0.1,
        "minimumDailyAccuracy": 0.3,
        "brierScore": brier,
        "logLoss": logloss,
        "expectedCalibrationError": ece,
    }


def test_sparse_fundamentals_are_ineligible():
    rows = [
        SimpleNamespace(
            features={
                "fundamentals_available": 1.0 if index < 3 else 0.0,
                "fundamentals_group_coverage": 1.0 if index < 3 else 0.0,
                "bbs_prior_available": 1.0 if index < 3 else 0.0,
            }
        )
        for index in range(100)
    ]
    proof = guard.feature_group_coverage(
        rows,
        group="market_temporal_team_fundamentals",
        feature_names=(
            "fundamentals_available",
            "fundamentals_group_coverage",
            "bbs_prior_available",
        ),
        training_partitions=(rows[:50], rows),
        validation_partitions=(rows[:50], rows[50:]),
    )
    assert proof["eligible"] is False
    assert any("fundamentals_available" in error for error in proof["errors"])


def test_candidate_requires_repeatable_fold_uplift():
    market = _metrics(0.55, 55)
    candidate = _metrics(0.58, 58)
    folds = [
        {
            "metrics": _metrics(0.60, 20, n=33),
            "marketBaseline": _metrics(0.55, 18, n=33),
        },
        {
            "metrics": _metrics(0.60, 20, n=33),
            "marketBaseline": _metrics(0.55, 18, n=33),
        },
        {
            "metrics": _metrics(0.45, 15, n=34),
            "marketBaseline": _metrics(0.55, 19, n=34),
        },
    ]
    proof = guard.candidate_stability(
        candidate,
        market,
        folds,
        calibration_eligible=objective.calibration_eligible,
    )
    assert proof["eligible"] is False
    assert "worst_fold_accuracy_regression_too_large" in proof["errors"]


def test_candidate_with_stable_uplift_is_eligible():
    market = _metrics(0.55, 55)
    candidate = _metrics(0.58, 58)
    folds = [
        {
            "metrics": _metrics(0.58, 19, n=33),
            "marketBaseline": _metrics(0.55, 18, n=33),
        },
        {
            "metrics": _metrics(0.58, 19, n=33),
            "marketBaseline": _metrics(0.55, 18, n=33),
        },
        {
            "metrics": _metrics(0.56, 19, n=34),
            "marketBaseline": _metrics(0.55, 18, n=34),
        },
    ]
    proof = guard.candidate_stability(
        candidate,
        market,
        folds,
        calibration_eligible=objective.calibration_eligible,
    )
    assert proof["eligible"] is True


def test_market_model_and_calibrator_preserve_probability():
    module = SimpleNamespace(VERSION="test")
    model = guard.MarketBaselineModel(module, seed=1)
    calibrator = guard.IdentityCalibrator()
    row = SimpleNamespace(market_probability=0.973)
    assert calibrator.apply(model.raw_probability(row)) == 0.973


def test_objective_install_remains_idempotent_for_contract_only_model():
    class Model:
        VERSION = "old"

        @staticmethod
        def _config_key(metrics, market):
            return (999.0,)

    first = objective.install(Model)
    second = objective.install(Model)
    assert first is second is Model
    assert Model.VERSION == objective.VERSION
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["marketBaselineFallbackEnabled"] is True
    assert Model.SUPERVISED_SELECTION_OBJECTIVE["productionAuthorityChanged"] is False


def _evaluate(examples, probabilities):
    correct = 0
    by_day = {}
    for row, probability in zip(examples, probabilities):
        hit = int((probability >= 0.5) == bool(row.outcome))
        correct += hit
        by_day.setdefault(row.day, []).append(hit)
    daily = [sum(values) / len(values) for values in by_day.values()]
    count = len(examples)
    return {
        "gameCount": count,
        "dayCount": len(by_day),
        "correct": correct,
        "overallAccuracy": correct / count if count else 0.0,
        "meanDailyAccuracy": sum(daily) / len(daily) if daily else 0.0,
        "minimumDailyAccuracy": min(daily) if daily else 0.0,
        "dailyPassRate": 0.0,
        "brierScore": sum(
            (probability - row.outcome) ** 2
            for row, probability in zip(examples, probabilities)
        )
        / count,
        "logLoss": 0.5,
        "expectedCalibrationError": 0.01,
    }


def test_installed_selector_falls_back_to_market_and_rejects_sparse_group():
    days = [f"2026-04-{index:02d}" for index in range(1, 7)]
    examples = []
    for day_index, day in enumerate(days):
        for game_index in range(10):
            outcome = (day_index + game_index) % 2
            examples.append(
                SimpleNamespace(
                    day=day,
                    outcome=outcome,
                    market_probability=0.75 if outcome else 0.25,
                    features={
                        "signal": 1.0,
                        "fundamentals_available": 1.0 if game_index == 0 else 0.0,
                        "fundamentals_group_coverage": 1.0 if game_index == 0 else 0.0,
                        "bbs_prior_available": 1.0 if game_index == 0 else 0.0,
                    },
                )
            )

    class Features:
        FEATURE_GROUPS = {
            "market": ("signal",),
            "market_temporal_team_fundamentals": (
                "signal",
                "fundamentals_available",
                "fundamentals_group_coverage",
                "bbs_prior_available",
            ),
        }

    class LearnedModel:
        def __init__(self, group):
            self.group = group

        def raw_probability(self, row):
            # Learned groups are intentionally worse than the market prior.
            return 1.0 - row.market_probability

    class Model:
        features = Features
        _INQSI_MLB_CALIBRATION_ELIGIBLE = staticmethod(
            lambda candidate_metrics, market_metrics: True
        )

        @staticmethod
        def nested_select(*args, **kwargs):
            raise AssertionError("guard did not replace selector")

        @staticmethod
        def fit_residual_logistic(
            examples, *, feature_group, l2, seed, **kwargs
        ):
            return LearnedModel(feature_group)

        @staticmethod
        def fit_platt(predictions, outcomes, **kwargs):
            return guard.IdentityCalibrator()

        @staticmethod
        def train_and_evaluate(records, **kwargs):
            return {}

        @staticmethod
        def inner_expanding_folds(train_days):
            return [
                (list(train_days[:2]), list(train_days[2:4])),
                (list(train_days[:4]), list(train_days[4:6])),
            ]

        @staticmethod
        def _subset(rows, selected_days):
            selected = set(selected_days)
            return [row for row in rows if row.day in selected]

        @staticmethod
        def evaluate_probabilities(rows, probabilities):
            return _evaluate(rows, probabilities)

        @staticmethod
        def _market_metrics(rows):
            return _evaluate(rows, [row.market_probability for row in rows])

    guard.install(Model)
    result = Model.nested_select(examples, days, seed=7)
    assert result["selectedFeatureGroup"] == guard.BASELINE_GROUP
    assert result["selectionGuard"]["baselineFallbackUsed"] is True
    sparse = result["ablation"]["market_temporal_team_fundamentals"]["guard"]
    assert sparse["eligible"] is False
    assert any("fundamentals" in error for error in sparse["errors"])
