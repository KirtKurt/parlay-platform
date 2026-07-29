from types import SimpleNamespace

from hello_world import mlb_supervised_selection_guard_v2_4 as guard


def _row(*, supported: bool, available: bool, v8: bool = True, f5: bool = False):
    return SimpleNamespace(
        features={
            "bbs_prior_supported": 1.0 if supported else 0.0,
            "bbs_prior_available": 1.0 if available else 0.0,
            "v8_available": 1.0 if v8 else 0.0,
            "v8_f5_available": 1.0 if f5 else 0.0,
        }
    )


def _supported_rows(count: int):
    return [
        _row(supported=True, available=(index % 5) < 3)
        for index in range(count)
    ]


def test_bbs_coverage_uses_supported_cohort_not_full_corpus():
    supported = _supported_rows(600)
    unsupported = [_row(supported=False, available=False) for _ in range(400)]
    proof = guard.feature_group_coverage(
        supported + unsupported,
        group="market_temporal_team_bbs_prior",
        feature_names=("bbs_prior_supported", "bbs_prior_available"),
        training_partitions=(supported[:300] + unsupported[:100], supported[:500]),
        validation_partitions=(supported[200:400], supported[400:600]),
    )

    assert proof["eligible"] is True
    assert proof["overall"]["bbs_prior_available"] == 0.6
    assert proof["denominators"]["bbs_prior_available"] == "bbs_prior_supported"
    assert proof["supportCounts"]["overall"] == 600


def test_bbs_supported_cohort_still_requires_absolute_evidence_floor():
    supported = [_row(supported=True, available=True) for _ in range(100)]
    unsupported = [_row(supported=False, available=False) for _ in range(900)]
    proof = guard.feature_group_coverage(
        supported + unsupported,
        group="market_temporal_team_bbs_prior",
        feature_names=("bbs_prior_supported", "bbs_prior_available"),
        training_partitions=(supported,),
        validation_partitions=(supported,),
    )

    assert proof["eligible"] is False
    assert "overall_bbs_supported_game_floor_not_met" in proof["errors"]
    assert "train_fold_1_bbs_supported_game_floor_not_met" in proof["errors"]
    assert "validation_fold_1_bbs_supported_game_floor_not_met" in proof["errors"]


def test_fullgame_v8_group_does_not_require_first_five_but_full_v8_does():
    rows = [_row(supported=True, available=True, v8=True, f5=False) for _ in range(600)]
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
    assert full_v8["requirements"]["v8_f5_available"] == guard.MIN_V8_FIRST_FIVE_COVERAGE
