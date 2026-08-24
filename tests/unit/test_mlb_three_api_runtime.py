from scripts.verify_mlb_three_api_runtime import verify


def test_three_api_autonomous_runtime_contract():
    result = verify()
    assert result["ok"] is True
    assert result["officialAuthority"] == "MLB Stats API"
    assert result["marketAuthority"] == "The Odds API"
    assert result["baseballContext"] == "Big Balls Data Pro"
    assert result["llm"] == "Amazon Bedrock"
    assert result["fullSlate"] is True
    assert result["predictionLeadMinutes"] == 45
    assert result["dailyAccuracyGoal"] == 0.70
