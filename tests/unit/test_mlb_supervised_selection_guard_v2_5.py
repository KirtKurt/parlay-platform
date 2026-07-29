from types import SimpleNamespace

from hello_world import mlb_supervised_selection_guard_v2_5 as guard


def _rows(
    count: int,
    *,
    supported: bool = True,
    available_count: int | None = None,
    v8: bool = True,
    first_five: bool = False,
):
    if available_count is None:
        available_count = count
    return [
        SimpleNamespace(
            features={
                "bbs_prior_supported": 1.0 if supported else 0.0,
                "bbs_prior_available": 1.0 if index < available_count else 0.0,
                "v8_available": 1.0 if v8 else 0.0,
                "v8_f5_available": 1.0 if first_five else 0.0,
            }
        )
        for index in range(count)
    ]


def test_provider_horizon_limited_fold_is_skipped_not_failed():
    training = (
        _rows(20, available_count=0),
        _rows(250, available_count=150),
        _rows(250, available_count=150),
    )
    validation = (
        _rows(200, available_count=120),
        _rows(200, available_count=120),
        _rows(200, available_count=120),
    )
    examples = [row for fold in training + validation for row in fold]

    proof = guard.feature_group_coverage(
        examples,
        group="market_temporal_team_bbs_prior",
        feature_names=("bbs_prior_supported", "bbs_prior_available"),
        training_partitions=training,
        validation_partitions=validation,
    )

    assert proof["eligible"] is True
    assert proof["errors"] == []
    assert proof["supportCounts"]["trainingFolds"] == [20, 250, 250]
    assert proof["supportCounts"]["trainingFoldEvaluable"] == [False, True, True]
    assert proof["supportCounts"]["evaluableTrainingFoldCount"] == 2
    assert proof["supportCounts"]["requiredEvaluableTrainingFoldCount"] == 2
    assert proof["supportCounts"]["skippedTrainingFolds"] == [1]
    assert proof["foldEvaluation"]["training"][0]["reason"] == (
        "provider_horizon_overlap_below_floor"
    )
    assert proof["foldEvaluation"]["unsupportedFoldsCountAsPassing"] is False


def test_too_few_evaluable_training_folds_fails_closed():
    training = (
        _rows(20),
        _rows(250),
        _rows(100),
    )
    validation = (
        _rows(200),
        _rows(200),
        _rows(200),
    )
    examples = [row for fold in training + validation for row in fold]

    proof = guard.feature_group_coverage(
        examples,
        group="market_temporal_team_bbs_prior",
        feature_names=("bbs_prior_supported", "bbs_prior_available"),
        training_partitions=training,
        validation_partitions=validation,
    )

    assert proof["eligible"] is False
    assert "bbs_evaluable_training_fold_count_below_2" in proof["errors"]
    assert proof["supportCounts"]["evaluableTrainingFoldCount"] == 1
    assert proof["supportCounts"]["skippedTrainingFolds"] == [1, 3]


def test_evaluable_fold_below_coverage_ratio_still_fails():
    training = (
        _rows(20, available_count=0),
        _rows(250, available_count=100),
        _rows(250, available_count=150),
    )
    validation = (
        _rows(200, available_count=120),
        _rows(200, available_count=120),
        _rows(200, available_count=120),
    )
    examples = [row for fold in training + validation for row in fold]

    proof = guard.feature_group_coverage(
        examples,
        group="market_temporal_team_bbs_prior",
        feature_names=("bbs_prior_supported", "bbs_prior_available"),
        training_partitions=training,
        validation_partitions=validation,
    )

    assert proof["eligible"] is False
    assert "train_fold_2_bbs_prior_available_below_0.50" in proof["errors"]
    assert "train_fold_1_bbs_prior_available_below_0.50" not in proof["errors"]
    assert "bbs_evaluable_training_fold_count_below_2" not in proof["errors"]


def test_fullgame_v8_group_remains_independent_of_first_five():
    rows = _rows(600, first_five=False)

    fullgame = guard.feature_group_coverage(
        rows,
        group="market_temporal_team_v8_fullgame",
        feature_names=("v8_available",),
        training_partitions=(rows[:300],),
        validation_partitions=(rows[300:],),
    )
    full_v8 = guard.feature_group_coverage(
        rows,
        group="market_temporal_team_v8",
        feature_names=("v8_available", "v8_f5_available"),
        training_partitions=(rows[:300],),
        validation_partitions=(rows[300:],),
    )

    assert fullgame["eligible"] is True
    assert "v8_f5_available" not in fullgame["requirements"]
    assert full_v8["eligible"] is False
    assert full_v8["requirements"]["v8_f5_available"] == (
        guard.MIN_V8_FIRST_FIVE_COVERAGE
    )
