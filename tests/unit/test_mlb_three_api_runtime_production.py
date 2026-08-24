from scripts.verify_mlb_three_api_runtime_production import verify


def test_production_three_source_contract_is_complete_and_idempotent():
    result = verify()
    assert result["ok"] is True
    assert result["contract"] == "MLB_THREE_API_AUTONOMOUS_RUNTIME_PRODUCTION"
    assert result["officialAuthority"] == "MLB Stats API"
    assert result["marketAuthority"] == "The Odds API"
    assert result["baseballContext"] == "Big Balls Data Pro"
    assert result["normalDeployIdempotent"] is True
    assert result["fullOfficialSlate"] is True
    assert result["noPass"] is True
    assert result["predictionLeadMinutes"] == 45
    assert result["completeCardDeadline"] == "second official game start minus 45 minutes"
    assert result["autonomousCadenceMinutes"] == 5
    assert result["postSettlementScoringAndRetraining"] is True
    assert result["dailyAccuracyGoal"] == 0.70
    assert result["fullOfficialSlateAccuracyDenominator"] is True
    assert result["accuracyGuarantee"] is False
    assert result["tennisAndSoccerIsolation"] is True
