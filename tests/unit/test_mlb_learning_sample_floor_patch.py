from types import SimpleNamespace

import mlb_learning_sample_floor_patch as patch


def _module(*, historical_rows: int, current_rows: int, adjustment: float = 4.25):
    state = {
        "historicalStats": {"historicalRowsUsed": historical_rows},
        "multiWindowStats": {"current24h": {"rowCount": current_rows}},
    }
    return SimpleNamespace(
        _latest_learning=lambda: state,
        _learning_adjustment=lambda tags: adjustment,
    )


def test_blocks_tiny_historical_cohort():
    module = patch.apply(_module(historical_rows=6, current_rows=0))
    assert module._learning_adjustment(["BOOK_AGREEMENT"]) == 0.0
    gate = module.mlbLearningSampleFloorStatus()
    assert gate["eligible"] is False
    assert "insufficient_clean_historical_rows" in gate["reasons"]


def test_blocks_small_nonempty_current_window():
    module = patch.apply(_module(historical_rows=30, current_rows=1))
    assert module._learning_adjustment(["REVERSAL"]) == 0.0
    assert module.mlbLearningSampleFloorStatus()["reasons"] == [
        "insufficient_current_24h_rows"
    ]


def test_allows_stable_historical_only_learning():
    module = patch.apply(_module(historical_rows=30, current_rows=0))
    assert module._learning_adjustment(["STEAM"]) == 4.25
    assert module.mlbLearningSampleFloorStatus()["eligible"] is True


def test_allows_learning_when_both_samples_clear_floor():
    module = patch.apply(_module(historical_rows=30, current_rows=10))
    assert module._learning_adjustment(["RUN_LINE_CONFIRMATION"]) == 4.25


def test_apply_is_idempotent():
    module = _module(historical_rows=30, current_rows=10)
    first = patch.apply(module)
    wrapped = first._learning_adjustment
    second = patch.apply(first)
    assert second is first
    assert second._learning_adjustment is wrapped
