import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hello_world"))

import mlb_autonomous_llm_decision_v1 as decision


class FakeBedrock:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        content = {
            "games": [
                {
                    "game_key": "g1",
                    "predicted_winner": "Boston Red Sox",
                    "home_win_probability": 0.30,
                    "confidence": "HIGH",
                    "primary_signals": ["supplemental context and market agreement"],
                    "source_use": {
                        "mlb_official": True,
                        "the_odds_api": True,
                        "big_balls_data": True,
                    },
                    "data_quality_notes": [],
                }
            ],
            "daily_accuracy_goal": 0.70,
        }
        return {
            "output": {"message": {"content": [{"text": json.dumps(content)}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }


def _payload():
    return {
        "picks": [
            {
                "id": "g1",
                "officialGamePk": "g1",
                "gameDateEt": "2026-08-24",
                "officialCommenceTime": "2026-08-24T23:00:00+00:00",
                "homeTeam": "New York Yankees",
                "awayTeam": "Boston Red Sox",
                "predictedWinner": "New York Yankees",
                "probability": 0.52,
                "bookmakers": [
                    {
                        "key": "book",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "New York Yankees", "price": 110},
                                    {"name": "Boston Red Sox", "price": -120},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _bbd_context():
    return {
        "available": True,
        "status": "READY",
        "fingerprint": "bbd-slate",
        "gameContexts": {
            "g1": {
                "homeTeam": "New York Yankees",
                "awayTeam": "Boston Red Sox",
                "datasetGroupsPresent": ["lineups", "injuries", "starters", "team_stats"],
                "datasets": {
                    "lineups": [{"confirmed": True}],
                    "injuries": [{"players": []}],
                    "starters": [{"home": "Starter A", "away": "Starter B"}],
                    "team_stats": [{"sample": "pregame"}],
                },
                "payloadFingerprint": "bbd-game",
            }
        },
    }


def test_three_sources_are_in_decision_path_and_can_change_close_model_pick():
    bedrock = FakeBedrock()
    result = decision.apply_to_prediction_payload(
        _payload(),
        now=datetime(2026, 8, 24, 16, 0, tzinfo=timezone.utc),
        bedrock_client=bedrock,
        bbd_slate_context=_bbd_context(),
    )
    pick = result["picks"][0]
    assert pick["predictedWinner"] == "Boston Red Sox"
    assert pick["autonomousLLMDecision"]["status"] == "APPLIED"
    assert pick["autonomousLLMDecision"]["threeSourceReady"] is True
    assert pick["autonomousLLMDecision"]["sourceUse"] == {
        "mlbOfficial": True,
        "theOddsApi": True,
        "bigBallsDataPro": True,
    }
    assert result["mlbAutonomousDecision"]["allGamesNoPass"] is True
    assert result["mlbAutonomousDecision"]["dailyAccuracyGoal"] == 0.70
    assert len(bedrock.calls) == 1
    prompt = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert "mlbOfficial" in prompt
    assert "theOddsApi" in prompt
    assert "bigBallsDataPro" in prompt


def test_post_start_prediction_is_never_recomputed():
    bedrock = FakeBedrock()
    result = decision.apply_to_prediction_payload(
        _payload(),
        now=datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc),
        bedrock_client=bedrock,
        bbd_slate_context=_bbd_context(),
    )
    assert result["picks"][0]["predictedWinner"] == "New York Yankees"
    assert result["picks"][0]["autonomousLLMDecision"]["status"] == "NO_POST_START_RECOMPUTE"
    assert bedrock.calls == []
