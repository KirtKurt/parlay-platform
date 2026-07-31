from __future__ import annotations

import scripts.run_mlb_historical_supervised_v9_no_bbd as runner


def _previous(feature_count: int = 100, fingerprint: str = "old"):
    return {
        "state": {"eligibleGameCount": 4000},
        "datasetFingerprint": "games",
        "lastShadowFitEligibleGameCount": 4000,
        "lastShadowFitDatasetFingerprint": "games",
        "lastLightweightEvaluationEligibleGameCount": 4000,
        "lastLightweightEvaluationDatasetFingerprint": "games",
        "lastShadowFitFeatureEligibleGameCount": feature_count,
        "lastShadowFitFeatureFingerprint": fingerprint,
        "lastLightweightFeatureEligibleGameCount": feature_count,
        "lastLightweightFeatureFingerprint": fingerprint,
    }


def test_ten_new_feature_rows_trigger_lightweight_evaluation(monkeypatch):
    monkeypatch.setenv("MLB_V7_FEATURE_REFIT_INCREMENT_ROWS", "50")
    monkeypatch.setenv("MLB_V7_FEATURE_LIGHTWEIGHT_INCREMENT_ROWS", "10")
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 110,
        "featureFingerprint": "new",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldLightweight"] is True
    assert decision["shouldRefit"] is False
    assert decision["featureCadenceTriggeredLightweightEvaluation"] is True


def test_fifty_new_feature_rows_trigger_full_refit(monkeypatch):
    monkeypatch.setenv("MLB_V7_FEATURE_REFIT_INCREMENT_ROWS", "50")
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 150,
        "featureFingerprint": "new",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True
    assert decision["featureCadenceTriggeredRefit"] is True


def test_no_feature_or_game_change_does_not_refit():
    runner._CONTEXT_PROOF = {
        "eligibleFeatureGameCount": 100,
        "featureFingerprint": "old",
    }
    decision = runner._feature_decision(
        _previous(),
        current_count=4000,
        fingerprint="games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is False
