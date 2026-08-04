from __future__ import annotations

from scripts import run_mlb_historical_supervised_v9_shadow_cadence_v3 as cadence


def _previous(*, games=4099, slates=331, features=120):
    return {
        "state": {
            "eligibleGameCount": games,
            "completeSlateCount": slates,
        },
        "datasetFingerprint": f"data-{games}",
        "featureCorpus": {
            "materializedFeatureRowCount": features,
            "fingerprint": f"features-{features}",
        },
        "lastShadowFitEligibleGameCount": games,
        "lastShadowFitDatasetFingerprint": f"data-{games}",
        "lastShadowFitFeatureRowCount": features,
        "lastShadowFitFeatureCorpusFingerprint": f"features-{features}",
        "lastLightweightEvaluationEligibleGameCount": games,
        "lastLightweightEvaluationDatasetFingerprint": f"data-{games}",
        "lastLightweightEvaluationFeatureRowCount": features,
        "lastLightweightEvaluationFeatureCorpusFingerprint": f"features-{features}",
        "lastShadowFitCompleteSlateCount": slates,
        "lastLightweightEvaluationCompleteSlateCount": slates,
        "shadowRefitPerformed": False,
        "lightweightSelectiveEvaluationPerformed": False,
    }


def _decide(previous, *, games, slates, features=120):
    return cadence.decide_cadence(
        previous,
        current_count=games,
        fingerprint=f"data-{games}",
        feature_count=features,
        feature_fingerprint=f"features-{features}",
        full_increment=50,
        lightweight_increment=25,
        full_feature_increment=50,
        lightweight_feature_increment=10,
        current_slate_count=slates,
        lightweight_slate_increment=1,
    )


def test_one_new_complete_slate_triggers_lightweight_without_full_refit():
    decision = _decide(_previous(), games=4107, slates=332)

    assert decision["newEligibleGamesSinceLastLightweightEvaluation"] == 8
    assert decision["newCompleteSlatesSinceLastLightweightEvaluation"] == 1
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is True
    assert "complete_slate_increment_reached" in decision["lightweightReasons"]


def test_hourly_evidence_without_new_slate_does_not_reset_or_trigger_anchor():
    previous = _previous()
    decision = _decide(previous, games=4099, slates=331)

    assert decision["newCompleteSlatesSinceLastLightweightEvaluation"] == 0
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is False

    report = cadence.report_anchor_fields(
        decision,
        current_count=4099,
        fingerprint="data-4099",
        feature_count=120,
        feature_fingerprint="features-120",
        current_slate_count=331,
        shadow_refit_performed=False,
        lightweight_performed=False,
    )
    assert report["lastLightweightEvaluationCompleteSlateCount"] == 331


def test_lightweight_evaluation_advances_only_lightweight_slate_anchor():
    previous = _previous(slates=330)
    decision = _decide(previous, games=4107, slates=331)
    report = cadence.report_anchor_fields(
        decision,
        current_count=4107,
        fingerprint="data-4107",
        feature_count=120,
        feature_fingerprint="features-120",
        current_slate_count=331,
        shadow_refit_performed=False,
        lightweight_performed=True,
    )

    assert report["lastShadowFitCompleteSlateCount"] == 330
    assert report["lastLightweightEvaluationCompleteSlateCount"] == 331
    assert report["remainingCompleteSlatesUntilLightweightEvaluation"] == 0


def test_complete_slate_regression_fails_closed_into_full_refit():
    decision = _decide(_previous(slates=331), games=4099, slates=330)

    assert decision["completeSlateCountRegressed"] is True
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True
    assert "complete_slate_count_regressed" in decision["refitReasons"]
