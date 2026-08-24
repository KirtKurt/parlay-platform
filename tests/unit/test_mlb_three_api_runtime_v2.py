from scripts.verify_mlb_three_api_runtime_v2 import verify


def test_mlb_three_api_runtime_v2_contract():
    result = verify()
    assert result["ok"] is True
    assert result["contract"] == "MLB_THREE_API_AUTONOMOUS_RUNTIME_v2"
    assert result["finalDecisionOverlayInstalled"] is True
    assert result["existingMlMateriallyUsed"] is True
    assert result["theOddsApiMateriallyUsed"] is True
    assert result["bigBallsDataProMateriallyUsedThroughContext"] is True
    assert result["bedrockLlmMateriallyUsed"] is True
    assert result["lockedPickEvidenceRequired"] is True
    assert result["statefulFiveMinuteController"] is True
    assert result["postSettlementRetraining"] is True
    assert result["fullOfficialSlateAccuracyDenominator"] is True
    assert result["dailyAccuracyGoal"] == 0.70
    assert result["accuracyGuarantee"] is False
