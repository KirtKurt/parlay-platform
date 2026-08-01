from scripts import run_mlb_historical_supervised_v9_shadow_cadence as cadence


def _report(previous, decision, *, current_count, fingerprint, refit=False, lightweight=False):
    value = {
        "state": {"eligibleGameCount": current_count},
        "datasetFingerprint": fingerprint,
        "shadowRefitPerformed": refit,
        "lightweightSelectiveEvaluationPerformed": lightweight,
    }
    value.update(
        cadence.report_anchor_fields(
            decision,
            current_count=current_count,
            fingerprint=fingerprint,
            shadow_refit_performed=refit,
            lightweight_performed=lightweight,
        )
    )
    return value


def test_refit_increment_accumulates_across_hourly_reports():
    previous = {
        "state": {"eligibleGameCount": 4000},
        "datasetFingerprint": "fit-4000",
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
    }

    first = cadence.decide_cadence(
        previous,
        current_count=4016,
        fingerprint="data-4016",
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
    )

    second = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
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
        lightweight=True,
    )

    third = cadence.decide_cadence(
        previous,
        current_count=4051,
        fingerprint="data-4051",
        full_increment=50,
        lightweight_increment=25,
    )
    assert third["newEligibleGamesSinceLastShadowFit"] == 51
    assert third["newEligibleGamesSinceLastLightweightEvaluation"] == 15
    assert third["shouldRefit"] is True
    assert third["shouldLightweight"] is True


def test_lightweight_evaluation_does_not_reset_full_refit_anchor():
    previous = {
        "state": {"eligibleGameCount": 4000},
        "datasetFingerprint": "fit-4000",
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4025,
        fingerprint="data-4025",
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
        lightweight=True,
    )
    assert report["lastShadowFitEligibleGameCount"] == 4000
    assert report["lastLightweightEvaluationEligibleGameCount"] == 4025

    next_decision = cadence.decide_cadence(
        report,
        current_count=4050,
        fingerprint="data-4050",
        full_increment=50,
        lightweight_increment=25,
    )
    assert next_decision["newEligibleGamesSinceLastShadowFit"] == 50
    assert next_decision["newEligibleGamesSinceLastLightweightEvaluation"] == 25
    assert next_decision["shouldRefit"] is True


def test_legacy_refit_report_seeds_accumulating_anchor():
    previous = {
        "state": {"eligibleGameCount": 3854},
        "datasetFingerprint": "fit-3854",
        "shadowRefitPerformed": True,
        "lightweightSelectiveEvaluationPerformed": True,
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["lastShadowFitEligibleGameCount"] == 3854
    assert decision["newEligibleGamesSinceLastShadowFit"] == 182
    assert decision["shouldRefit"] is True


def test_legacy_waiting_report_recovers_recorded_delta_and_fit_fingerprint():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "data-4036",
        "shadowRefitPerformed": False,
        "lightweightSelectiveEvaluationPerformed": False,
        "newEligibleGamesSinceLastShadowFit": 16,
        "previousShadowDatasetFingerprint": "fit-4020",
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="data-4036",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["lastShadowFitEligibleGameCount"] == 4020
    assert decision["lastShadowFitDatasetFingerprint"] == "fit-4020"
    assert decision["newEligibleGamesSinceLastShadowFit"] == 16


def test_force_always_runs_full_and_lightweight_learning():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "same",
        "lastShadowFitEligibleGameCount": 4036,
        "lastShadowFitDatasetFingerprint": "same",
        "lastLightweightEvaluationEligibleGameCount": 4036,
        "lastLightweightEvaluationDatasetFingerprint": "same",
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="same",
        full_increment=50,
        lightweight_increment=25,
        force=True,
    )
    assert decision["newEligibleGamesSinceLastShadowFit"] == 0
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True


def test_feature_only_fingerprint_change_triggers_immediate_refit():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "fit-with-100-context-games",
        "lastShadowFitEligibleGameCount": 4036,
        "lastShadowFitDatasetFingerprint": "fit-with-100-context-games",
        "lastLightweightEvaluationEligibleGameCount": 4036,
        "lastLightweightEvaluationDatasetFingerprint": "fit-with-100-context-games",
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4036,
        fingerprint="same-games-with-110-context-games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["newEligibleGamesSinceLastShadowFit"] == 0
    assert decision["featureOnlyDatasetChangeSinceLastShadowFit"] is True
    assert decision["featureOnlyDatasetChangeSinceLastLightweightEvaluation"] is True
    assert decision["shouldRefit"] is True
    assert decision["shouldLightweight"] is True

    report = _report(
        previous,
        decision,
        current_count=4036,
        fingerprint="same-games-with-110-context-games",
        refit=True,
        lightweight=True,
    )
    next_decision = cadence.decide_cadence(
        report,
        current_count=4036,
        fingerprint="same-games-with-110-context-games",
        full_increment=50,
        lightweight_increment=25,
    )
    assert next_decision["shouldRefit"] is False
    assert next_decision["shouldLightweight"] is False


def test_small_game_increment_still_uses_normal_thresholds():
    previous = {
        "state": {"eligibleGameCount": 4036},
        "datasetFingerprint": "fit-4036",
        "lastShadowFitEligibleGameCount": 4036,
        "lastShadowFitDatasetFingerprint": "fit-4036",
        "lastLightweightEvaluationEligibleGameCount": 4036,
        "lastLightweightEvaluationDatasetFingerprint": "fit-4036",
    }
    decision = cadence.decide_cadence(
        previous,
        current_count=4037,
        fingerprint="data-4037",
        full_increment=50,
        lightweight_increment=25,
    )
    assert decision["featureOnlyDatasetChangeSinceLastShadowFit"] is False
    assert decision["shouldRefit"] is False
    assert decision["shouldLightweight"] is False
