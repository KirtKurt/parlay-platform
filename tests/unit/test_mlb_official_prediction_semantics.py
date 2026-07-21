from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_prediction_semantics as semantics
import mlb_official_freeze_bridge as freeze_bridge


def _locked_row(**overrides):
    row = {
        "gameId": "game-1",
        "homeTeam": "Home Club",
        "awayTeam": "Away Club",
        "predictedWinner": "Home Club",
        "predictedSide": "home",
        "teamWinProbabilityPct": 51.2,
        "lockedPrediction": True,
        "playable": False,
        "playablePick": False,
        "actionablePick": False,
        "tags": ["FINAL_LOCKED", "NOT_PLAYABLE"],
    }
    row.update(overrides)
    return row


def test_blocked_locked_prediction_remains_official_and_settlement_eligible():
    result = semantics.enhance_result({
        "predictions": [_locked_row(
            blocked=True,
            playable=True,
            actionablePick=True,
            playabilityBlockReasons=["CONFIRMED_IMPACT_PLAYER_ABSENCE"],
            trainingEligible=False,
        )]
    })
    row = result["predictions"][0]

    assert row["predictedWinner"] == "Home Club"
    assert row["predictedSide"] == "home"
    assert row["lockedPrediction"] is True
    assert row["officialPrediction"] is True
    assert row["officialPick"] is True
    assert row["officialPredictionStatus"] == "OFFICIAL_LOCKED_PREDICTION"
    assert row["blocked"] is True
    assert row["releaseBlocked"] is True
    assert row["wagerReleaseBlocked"] is True
    assert row["playable"] is False
    assert row["actionablePick"] is False
    assert row["playabilityStatus"] == "BLOCKED"
    assert row["releaseBlockReasons"] == ["CONFIRMED_IMPACT_PLAYER_ABSENCE"]
    assert row["officialAccuracyEligible"] is True
    assert row["officialOutcomeAuditEligible"] is True
    assert row["settlementEligible"] is True
    assert row["playableAccuracyEligible"] is False
    assert row["accuracyTargetEligible"] is False
    assert row["trainingEligible"] is False
    assert row["trainingEligibilityStatus"] == "INELIGIBLE"

    assert result["officialPredictionCount"] == 1
    assert result["blockedOfficialPredictionCount"] == 1
    assert result["officialAccuracyEligibleCount"] == 1
    assert result["settlementEligibleCount"] == 1
    assert result["playableAccuracyEligibleCount"] == 0
    assert result["trainingEligibleOfficialPredictionCount"] == 0


def test_low_confidence_nonplayable_lock_can_still_be_training_eligible():
    result = semantics.enhance_result({
        "predictions": [_locked_row(
            mlFeatureFreeze={
                "trainingEligible": True,
                "trainingExclusionReasons": [],
            },
        )]
    })
    row = result["predictions"][0]

    assert row["officialPrediction"] is True
    assert row["playable"] is False
    assert row["blocked"] is False
    assert row["trainingEligible"] is True
    assert row["trainingEligibilityStatus"] == "ELIGIBLE"
    assert row["officialAccuracyEligible"] is True
    assert row["settlementEligible"] is True
    assert row["playableAccuracyEligible"] is False
    assert result["trainingEligibleOfficialPredictionCount"] == 1
    assert result["nonPlayableOfficialPredictionCount"] == 1


def test_playable_accuracy_is_a_subset_of_official_accuracy():
    result = semantics.enhance_result({
        "predictions": [
            _locked_row(
                gameId="playable",
                playable=True,
                playablePick=True,
                actionablePick=True,
                tags=["FINAL_LOCKED", "ACTIONABLE_PICK"],
                trainingEligible=False,
            ),
            _locked_row(gameId="not-playable", trainingEligible=True),
        ]
    })

    playable, not_playable = result["predictions"]
    assert playable["officialPrediction"] is True
    assert playable["playable"] is True
    assert playable["officialAccuracyEligible"] is True
    assert playable["playableAccuracyEligible"] is True
    assert playable["settlementEligible"] is True
    assert playable["accuracyTargetEligible"] is True
    assert playable["trainingEligible"] is False

    assert not_playable["officialPrediction"] is True
    assert not_playable["playableAccuracyEligible"] is False
    assert not_playable["settlementEligible"] is True
    assert not_playable["trainingEligible"] is True

    assert result["officialAccuracyEligibleCount"] == 2
    assert result["settlementEligibleCount"] == 2
    assert result["playableAccuracyEligibleCount"] == 1
    assert result["trainingEligibleOfficialPredictionCount"] == 1


def test_prelock_playability_does_not_create_official_accuracy_or_settlement_authority():
    result = semantics.enhance_result({
        "slatePredictionLock": {"locked": False},
        "predictions": [{
            "gameId": "prelock",
            "predictedWinner": "Away Club",
            "predictedSide": "away",
            "playable": True,
            "actionablePick": True,
            "trainingEligible": True,
            "tags": ["ACTIONABLE_PICK"],
        }],
    })
    row = result["predictions"][0]

    assert row["officialPrediction"] is False
    assert row["officialPick"] is False
    assert row["playable"] is True
    assert row["accuracyTargetEligible"] is True
    assert row["officialAccuracyEligible"] is False
    assert row["officialOutcomeAuditEligible"] is False
    assert row["settlementEligible"] is False
    assert row["playableAccuracyEligible"] is False
    assert row["trainingEligible"] is True


def test_freeze_bridge_never_synthesizes_vector_or_rewrites_training_status():
    module = SimpleNamespace(enhance_result=semantics.enhance_result)
    freeze_bridge.apply(module)
    source = {
        "predictions": [{
            "gameId": "legacy-read-row",
            "predictedWinner": "Home Club",
            "predictedSide": "home",
            "lockedPrediction": True,
            "trainingEligible": True,
            "mlFeatureFreeze": {"trainingEligible": True},
        }]
    }

    result = module.enhance_result(source)
    row = result["predictions"][0]

    assert row["trainingEligible"] is True
    assert "frozenFeatureVector" not in row
    assert result["mlFeatureFreeze"]["readPathMayCreateOrRewriteVector"] is False


def test_legacy_intentional_block_is_release_only_for_an_existing_lock():
    source = _locked_row(
        predictionIntentionallyBlocked=True,
        predictionBlockStatus="INTENTIONAL_POLICY_BLOCK",
        predictionBlockReason="LEGACY_HARD_CONFIDENCE_GATE",
    )
    first = semantics.enhance_result({"predictions": [source]})
    second = semantics.enhance_result(first)
    row = second["predictions"][0]

    assert row["predictedWinner"] == "Home Club"
    assert row["officialPrediction"] is True
    assert row["blocked"] is True
    assert row["playable"] is False
    assert row["settlementEligible"] is True
    assert row["releaseBlockReasons"] == ["LEGACY_HARD_CONFIDENCE_GATE"]
    assert "OFFICIAL_LOCKED_PREDICTION" in row["tags"]
    assert "RELEASE_BLOCKED" in row["tags"]
    assert second["officialPredictionCount"] == 1
    assert second["blockedOfficialPredictionCount"] == 1


def test_legacy_aliases_and_display_contract_remain_available():
    result = semantics.enhance_result({
        "modelVersion": "legacy-model",
        "predictions": [_locked_row()],
    })
    row = result["predictions"][0]
    card = result["officialPredictionDisplay"][0]

    assert row["officialPick"] == row["officialPrediction"] is True
    assert row["playablePick"] == row["playable"] is False
    assert row["actionablePick"] == row["accuracyTargetEligible"] is False
    assert row["displayGroup"] == "official_non_playable_prediction"
    assert row["recommendationStatus"] == "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    assert result["officialPickCount"] == result["officialPredictionCount"] == 1
    assert result["actionablePickCount"] == result["playablePredictionCount"] == 0
    assert card["officialPrediction"] is True
    assert card["playable"] is False
    assert card["settlementEligible"] is True
    assert semantics.VERSION in result["modelVersion"]
