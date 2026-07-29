from __future__ import annotations

from collections import Counter, defaultdict
from types import SimpleNamespace

from hello_world import mlb_supervised_selection_guard_v2_3 as base
from hello_world import mlb_supervised_selection_guard_v2_6 as guard


def _metrics(rows, probabilities):
    count = len(rows)
    correct = sum(
        int((probability >= 0.5) == bool(row.outcome))
        for row, probability in zip(rows, probabilities)
    )
    return {
        "gameCount": count,
        "dayCount": len({row.day for row in rows}),
        "correct": correct,
        "overallAccuracy": correct / count if count else 0.0,
        "meanDailyAccuracy": correct / count if count else 0.0,
        "minimumDailyAccuracy": 0.0,
        "dailyPassRate": 0.0,
        "brierScore": 0.25,
        "logLoss": 0.69,
        "expectedCalibrationError": 0.01,
    }


def test_v26_installs_bounded_seed_aligned_regularization_grid():
    class Model:
        @staticmethod
        def nested_select(*_args, **_kwargs):
            return {"selectionGuard": {"thresholds": {}}}

    guard.install(Model)
    result = Model.nested_select()

    assert tuple(base.REGULARIZATION_GRID) == guard.REGULARIZATION_GRID
    assert result["selectionGuard"]["version"] == guard.VERSION
    thresholds = result["selectionGuard"]["thresholds"]
    assert thresholds["regularizationGrid"] == list(guard.REGULARIZATION_GRID)
    assert thresholds["regularizationComparisonSeedAligned"] is True
    assert thresholds["regularizationGridBounded"] is True


def test_selector_executes_every_l2_with_identical_seed_per_fold():
    days = [f"2026-04-{index:02d}" for index in range(1, 7)]
    rows = [
        SimpleNamespace(
            day=day,
            outcome=(day_index + game_index) % 2,
            market_probability=0.55,
            features={"signal": 1.0},
        )
        for day_index, day in enumerate(days)
        for game_index in range(4)
    ]
    fit_calls = []

    class Features:
        FEATURE_GROUPS = {"market": ("signal",)}

    class LearnedModel:
        def raw_probability(self, row):
            return row.market_probability

    class Model:
        features = Features
        _INQSI_MLB_CALIBRATION_ELIGIBLE = staticmethod(lambda *_args: True)

        @staticmethod
        def nested_select(*_args, **_kwargs):
            raise AssertionError("selection guard was not installed")

        @staticmethod
        def fit_residual_logistic(examples, *, feature_group, l2, seed, **kwargs):
            fit_calls.append((feature_group, float(l2), int(seed)))
            return LearnedModel()

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
        def _subset(examples, selected_days):
            selected = set(selected_days)
            return [row for row in examples if row.day in selected]

        @staticmethod
        def evaluate_probabilities(examples, probabilities):
            return _metrics(examples, probabilities)

        @staticmethod
        def _market_metrics(examples):
            return _metrics(examples, [row.market_probability for row in examples])

    guard.install(Model)
    result = Model.nested_select(rows, days, seed=7)

    assert result["candidateCount"] == 2 * len(guard.REGULARIZATION_GRID)
    assert Counter(l2 for _, l2, _ in fit_calls) == {
        value: 2 for value in guard.REGULARIZATION_GRID
    }
    seeds_by_l2 = defaultdict(list)
    for _, l2, seed in fit_calls:
        seeds_by_l2[l2].append(seed)
    assert all(seeds == [7, 8] for seeds in seeds_by_l2.values())


def test_regularization_grid_is_ordered_unique_and_positive():
    values = guard.REGULARIZATION_GRID
    assert values == tuple(sorted(set(values)))
    assert len(values) == 8
    assert all(value > 0.0 for value in values)
    assert values[0] >= 0.005
    assert values[-1] <= 1.0


def test_v26_install_is_idempotent():
    class Model:
        @staticmethod
        def nested_select(*_args, **_kwargs):
            return {"selectionGuard": {"thresholds": {}}}

    first = guard.install(Model)
    wrapped = Model.nested_select
    second = guard.install(Model)

    assert first is second is Model
    assert Model.nested_select is wrapped
