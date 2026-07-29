from __future__ import annotations

from hello_world import mlb_supervised_selection_guard_v2_3 as base
from hello_world import mlb_supervised_selection_guard_v2_6 as guard


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


def test_regularization_grid_is_ordered_unique_and_positive():
    values = guard.REGULARIZATION_GRID
    assert values == tuple(sorted(set(values)))
    assert len(values) >= 6
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
