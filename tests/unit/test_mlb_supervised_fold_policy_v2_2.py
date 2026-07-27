from __future__ import annotations

from datetime import date, timedelta

from hello_world import mlb_supervised_fold_policy_v2_2 as policy
from hello_world import mlb_supervised_model_v2 as base


def test_current_season_fold_is_strictly_chronological_and_recent():
    start = date(2025, 4, 1)
    days = [(start + timedelta(days=index)).isoformat() for index in range(190)]
    days.extend(
        (date(2026, 3, 25) + timedelta(days=index)).isoformat()
        for index in range(12)
    )
    folds = policy.current_season_folds(base.inner_expanding_folds, days)
    assert len(folds) >= 2
    fit, validation = folds[-1]
    assert validation == sorted(days)[-5:]
    assert max(fit) < min(validation)
    assert all(value.startswith("2026-") for value in validation)
    assert set(fit).isdisjoint(validation)


def test_fold_policy_falls_back_before_current_season_has_enough_dates():
    days = [
        (date(2025, 4, 1) + timedelta(days=index)).isoformat()
        for index in range(90)
    ]
    days.extend(
        (date(2026, 3, 25) + timedelta(days=index)).isoformat()
        for index in range(4)
    )
    original = base.inner_expanding_folds(days)
    actual = policy.current_season_folds(base.inner_expanding_folds, days)
    assert actual == original


def test_install_is_idempotent():
    class Model:
        _INQSI_CURRENT_SEASON_FOLDS_V2_2_INSTALLED = False

        @staticmethod
        def inner_expanding_folds(days, *, fold_count=3):
            del fold_count
            return [([days[0]], [days[-1]])]

    first = policy.install(Model)
    second = policy.install(Model)
    assert first is second is Model
    assert Model.CURRENT_SEASON_FOLD_POLICY_VERSION == policy.VERSION
