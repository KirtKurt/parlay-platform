from __future__ import annotations

import os
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HELLO = ROOT / "hello_world"
if str(HELLO) not in sys.path:
    sys.path.insert(0, str(HELLO))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ["INQSI_MLB_V2_INFERENCE_ENABLED"] = "true"
os.environ["INQSI_DEPLOY_GIT_SHA"] = "a" * 40
os.environ["INQSI_DEPLOY_TEMPLATE_SHA256"] = "b" * 64

import mlb_ml_v2_inference_consumer_v1 as consumer


def _champion():
    challenger = {
        "ok": True,
        "selectedThreshold": 0.7,
        "thresholdSelectionSource": "validation_only_before_prospective_cutover",
    }
    champion = {
        "recordType": "mlb_ml_active_champion_v2",
        "artifactDigest": "candidate-1",
        "runtimeAuthorityActivated": True,
        "stableChampion": True,
        "shadowOnly": False,
        "directionAuthorityEnabled": True,
        "playabilityAuthorityEnabled": True,
        "automaticWagerAllowed": False,
        "deploymentIdentity": {
            "gitSha": "a" * 40,
            "templateSha256": "b" * 64,
        },
        "frozenChallenger": challenger,
        "frozenChallengerSha256": consumer._sha256(challenger),
        "promotionGate": {
            "promotionEligible": True,
            "testWasUntouched": True,
            "calibrationAndProperScoringRequired": True,
        },
    }
    return champion


def test_validate_champion_requires_exact_deployment_identity():
    champion = _champion()
    accepted, status = consumer._validate_champion(champion)
    assert accepted is not None
    assert status["status"] == "ACTIVE_CHAMPION_READY"

    champion["deploymentIdentity"]["gitSha"] = "c" * 40
    accepted, status = consumer._validate_champion(champion)
    assert accepted is None
    assert "champion_runtime_deployment_identity_mismatch" in status["errors"]


def test_direction_consumer_changes_winner_but_not_playability(monkeypatch):
    fake_dual = types.SimpleNamespace(
        score_unlabeled_lock=lambda row, challenger: {
            "outcomeProbability": 0.72,
            "reliabilityProbability": 0.81,
        }
    )
    monkeypatch.setitem(sys.modules, "mlb_ml_dual_model_v2", fake_dual)

    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {
                "ok": True,
                "modelVersion": "incumbent",
                "predictions": [
                    {
                        "gameId": "g1",
                        "homeTeam": "Home",
                        "awayTeam": "Away",
                        "predictedWinner": "Away",
                        "predictedSide": "away",
                        "opponent": "Home",
                        "promoted": True,
                        "promotionStatus": "PROMOTED",
                        "tags": [],
                        "homeSignal": {
                            "fairProbability": 0.55,
                            "americanOdds": -125,
                            "priceBook": "book",
                            "priceSource": "real_book",
                            "marketSide": "favorite",
                        },
                        "awaySignal": {
                            "fairProbability": 0.45,
                            "americanOdds": 115,
                            "priceBook": "book",
                            "priceSource": "real_book",
                            "marketSide": "underdog",
                        },
                    }
                ],
            }

    champion = _champion()
    engine = Engine()
    consumer.apply_direction(
        engine,
        champion_loader=lambda: (
            champion,
            {"ok": True, "status": "ACTIVE_CHAMPION_READY"},
        ),
    )
    result = engine.predict_all()
    row = result["predictions"][0]

    assert row["predictedWinner"] == "Home"
    assert row["predictedSide"] == "home"
    assert row["opponent"] == "Away"
    assert row["winProbability"] == 0.72
    assert row["v2DirectionChanged"] is True
    assert row["v2ReliabilitySelected"] is True
    assert row["v2PlayabilityCandidate"] is True
    assert row["promoted"] is False
    assert row["automaticWagerAllowed"] is False
    assert "MLB_V2_DIRECTION_AUTHORITY" in row["tags"]


def test_no_champion_preserves_incumbent_prediction():
    class Engine:
        @staticmethod
        def predict_all(*args, **kwargs):
            return {
                "ok": True,
                "predictions": [
                    {
                        "gameId": "g1",
                        "homeTeam": "Home",
                        "awayTeam": "Away",
                        "predictedWinner": "Away",
                    }
                ],
            }

    engine = Engine()
    consumer.apply_direction(
        engine,
        champion_loader=lambda: (
            None,
            {"ok": True, "status": "NO_ACTIVE_CHAMPION"},
        ),
    )
    result = engine.predict_all()
    assert result["predictions"][0]["predictedWinner"] == "Away"
    assert result["v2InferenceAuthorityAppliedCount"] == 0
