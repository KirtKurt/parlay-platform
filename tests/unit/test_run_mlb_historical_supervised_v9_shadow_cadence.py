import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence


def _report(
    previous,
    decision,
    *,
    current_count,
    fingerprint,
    feature_count=0,
    feature_fingerprint="features-none",
    refit=False,
    lightweight=False,
):
    value = {
        "state": {"eligibleGameCount": current_count},
        "datasetFingerprint": fingerprint,
        "featureCorpus": {
            "materializedFeatureRowCount": feature_count,
            "fingerprint": feature_fingerprint,
        },
        "shadowRefitPerformed": refit,
        "lightweightSelectiveEvaluationPerformed": lightweight,
    }
    value.update(
        cadence.report_anchor_fields(
            decision,
            current_count=current_count,
            fingerprint=fingerprint,
            feature_count=feature_count,
            feature_fingerprint=feature_fingerprint,
            shadow_refit_performed=refit,
            lightweight_performed=lightweight,
        )
    )
    return value


def _initial_report(*, games=4000, features=100):
    return {
        "state": {"eligibleGameCount": games},
        "datasetFingerprint": f"fit-{games}",
        "featureCorpus": {
            "materializedFeatureRowCount": features,
            "fingerprint": f"features-{features}",
        },
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
    }


def test_refit_increment_accumulates_across_hourly_reports():
    previous = _initial_report()
    first = cadence.decide_cadence(
        previous,
        current_count=4016,
        fingerprint="data-4016",
        feature_count=100,
        feature_fingerprint="features-100",
        full_increment=50,
        lightweight_increment=25,
    )
    assert first["newEligibleGamesSinceLastShadowFit"] == 16
    assert first["shouldRefit"] is False
    assert first["shouldLightweight"] is False
    previous = _report(
        previous,
        first,
        current_count=4016,
        fingerprint="data-4016",
        feature_count=100,
    )

    second = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
        feature_count=100,
        feature_fingerprint="features-100",
        full_increment=50,
        lightweight_increment=25,
    )
    assert second["newEligibleGamesSinceLastShadowFit"] == 36
    assert second["shouldRefit"] is False
    assert second["shouldLightweight"] is True
    previous = _report(
        previous,
        second,
        current_count=4036,
        fingerprint="data-4036",
        feature_count=100,
        lightweight=True,
    )

    third = cadence.decide_cadence(
        previous,
        current_count=4051,
        fingerprint="data-4051",
        feature_count=100,
        feature_fingerprint="features-100",
        full_increment=50,
        lightweight_increment=25,
    )
    assert third["newEligibleGamesSinceLastShadowFit"] == 51
    assert third["newEligibleGamesSinceLastLightweightEvaluation"] == 15
    assert third["shouldRefit"] is True
    assert third["shouldLightweight"] is True


def test_ten_new_feature_rows_trigger_lightweight_without_new_games():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=120,
        feature_fingerprint="features-120",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert decision["newFeatureRowsSinceLastShadowFit"] == 10
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is True
    assert decision["lightweightReasons"] == ["feature_row_increment_reached"]


def test_fifty_new_feature_rows_trigger_full_refit_without_new_games():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=160,
        feature_fingerprint="features-160",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert decision["newFeatureRowsSinceLastShadowFit"] == 50
    assert decision["shouldRefit"] is True
    assert "feature_row_increment_reached" in decision["refitReasons"]


def test_feature_only_hourly_reports_do_not_reset_full_refit_anchor():
    previous = _initial_report(games=4036, features=100)
    first = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=110,
        feature_fingerprint="features-110",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    report = _report(
        previous,
        first,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=110,
        feature_fingerprint="features-110",
        lightweight=True,
    )
    assert report["lastShadowFitFeatureRowCount"] == 100
    assert report["lastLightweightEvaluationFeatureRowCount"] == 110

    second = cadence.decide_cadence(
        report,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=150,
        feature_fingerprint="features-150",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert second["newFeatureRowsSinceLastShadowFit"] == 50
    assert second["shouldRefit"] is True


def test_semantic_feature_rewrite_at_same_count_triggers_full_refit():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=110,
        feature_fingerprint="features-110-corrected",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert decision["shouldRefit"] is True
    assert "feature_corpus_rewritten_at_same_count" in decision["refitReasons"]


def test_feature_count_regression_is_not_silently_ignored():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=100,
        feature_fingerprint="features-100",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
    )
    assert decision["featureRowCountRegressed"] is True
    assert decision["shouldRefit"] is True
    assert "training_corpus_regressed" in decision["refitReasons"]


def test_force_always_runs_full_and_lightweight_learning():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036",
        feature_count=110,
        feature_fingerprint="features-110",
        full_increment=50,
        lightweight_increment=25,
        force=True,
    )
    assert decision["newEligibleGamesSinceLastShadowFit"] == 0
    assert decision["newFeatureRowsSinceLastShadowFit"] == 0
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True


def test_canonical_dataset_rewrite_at_same_game_count_triggers_refit():
    previous = _initial_report(games=4036, features=110)
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="fit-4036-corrected",
        feature_count=110,
        feature_fingerprint="features-110",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldRefit"] is True
    assert "canonical_dataset_rewritten_at_same_count" in decision["refitReasons"]


def test_lightweight_evaluation_does_not_reset_full_game_refit_anchor():
    previous = _initial_report(games=4000, features=100)
    decision = cadence.decide_cadence(
        previous,
        current_count=4025,
        fingerprint="data-4025",
        feature_count=100,
        feature_fingerprint="features-100",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldLightweight"] is True
    assert decision["shouldRefit"] is False
    report = _report(
        previous,
        decision,
        current_count=4025,
        fingerprint="data-4025",
        feature_count=100,
        lightweight=True,
    )
    assert report["lastShadowFitEligibleGameCount"] == 4000
    assert report["lastLightweightEvaluationEligibleGameCount"] == 4025


def test_legacy_refit_report_seeds_game_and_feature_anchors():
    previous = {
        "state": {"eligibleGameCount": 3854},
        "datasetFingerprint": "fit-3854",
        "featureCorpus": {
            "materializedFeatureRowCount": 60,
            "fingerprint": "features-60",
        },
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
        feature_count=60,
        feature_fingerprint="features-60",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["lastShadowFitEligibleGameCount"] == 3854
    assert decision["lastShadowFitFeatureRowCount"] == 60
    assert decision["newEligibleGamesSinceLastShadowFit"] == 182
    assert decision["shouldRefit"] is True


def test_legacy_waiting_report_recovers_recorded_game_delta():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "data-4036",
        "featureCorpus": {
            "materializedFeatureRowCount": 110,
            "fingerprint": "features-110",
        },
        "shadowRefitPerformed": False,
        "lightweightSelectiveEvaluationPerformed": False,
        "newEligibleGamesSinceLastShadowFit": 16,
        "previousShadowDatasetFingerprint": "fit-4020",
        "lastShadowFitFeatureRowCount": 110,
        "lastShadowFitFeatureCorpusFingerprint": "features-110",
        "lastLightweightEvaluationFeatureRowCount": 110,
        "lastLightweightEvaluationFeatureCorpusFingerprint": "features-110",
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
        feature_count=110,
        feature_fingerprint="features-110",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["lastShadowFitEligibleGameCount"] == 4020
    assert decision["lastShadowFitDatasetFingerprint"] == "fit-4020"
    assert decision["newEligibleGamesSinceLastShadowFit"] == 16


def test_legacy_waiting_report_without_feature_anchor_forces_migration_refit():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "same",
        "lastShadowFitEligibleGameCount": 4036,
        "lastShadowFitDatasetFingerprint": "same",
        "lastLightweightEvaluationEligibleGameCount": 4036,
        "lastLightweightEvaluationDatasetFingerprint": "same",
        "featureCorpus": {
            "materializedFeatureRowCount": 110,
            "fingerprint": "features-110",
        },
        "shadowRefitPerformed": False,
        "lightweightSelectiveEvaluationPerformed": False,
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="same",
        feature_count=110,
        feature_fingerprint="features-110",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["lastShadowFitFeatureRowCount"] == 0
    assert decision["shouldRefit"] is True
    assert "missing_refit_anchor" in decision["refitReasons"]
