from scripts.decide_mlb_v10_dispatch import decide


def historical(**overrides):
    state = {
        "eligibleGameCount": 4210,
        "completeSlateCount": 339,
        "featureRematerializedSlateCount": 339,
        "featureDatasetVersion": "FEATURES-v9",
    }
    state.update(overrides)
    return {"ok": True, "state": state}


def report(**overrides):
    anchor = {
        "eligibleGameCount": 4210,
        "completeSlateCount": 339,
        "featureRematerializedSlateCount": 339,
        "featureDatasetVersion": "FEATURES-v9",
    }
    anchor.update(overrides)
    return {"ok": True, "cadenceAnchor": anchor}


def test_current_material_state_does_not_dispatch():
    result = decide(historical(), report())
    assert result["ok"] is True
    assert result["dispatchRequired"] is False
    assert result["changedFields"] == []
    assert result["reason"] == "V10_MATERIAL_STATE_CURRENT"


def test_new_games_and_slate_dispatch_v10():
    result = decide(
        historical(
            eligibleGameCount=4225,
            completeSlateCount=340,
            featureRematerializedSlateCount=340,
        ),
        report(),
    )
    assert result["ok"] is True
    assert result["dispatchRequired"] is True
    assert result["changedFields"] == [
        "completeSlateCount",
        "eligibleGameCount",
        "featureRematerializedSlateCount",
    ]


def test_feature_dataset_version_change_dispatches_without_new_games():
    result = decide(historical(featureDatasetVersion="FEATURES-v10"), report())
    assert result["ok"] is True
    assert result["dispatchRequired"] is True
    assert result["changedFields"] == ["featureDatasetVersion"]


def test_missing_or_invalid_v10_report_bootstraps_dispatch():
    result = decide(historical(), None)
    assert result["ok"] is True
    assert result["dispatchRequired"] is True
    assert result["changedFields"] == ["v10_report_missing_or_invalid"]


def test_historical_regression_fails_closed_without_dispatch():
    result = decide(historical(eligibleGameCount=4200), report())
    assert result["ok"] is False
    assert result["dispatchRequired"] is False
    assert result["reason"] == "HISTORICAL_MATERIAL_STATE_INVALID_OR_REGRESSED"
    assert result["blockers"] == [
        "historical_state_regressed:eligibleGameCount:4200<4210"
    ]


def test_missing_material_anchor_fails_closed():
    result = decide(historical(featureRematerializedSlateCount=0), report())
    assert result["ok"] is False
    assert result["dispatchRequired"] is False
    assert (
        "historical_anchor_missing:featureRematerializedSlateCount"
        in result["blockers"]
    )
