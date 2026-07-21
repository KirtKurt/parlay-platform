#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_official_prediction_semantics as semantics
import mlb_real_world_accuracy_patch as accuracy
import mlb_real_world_accuracy_semantics_fix as accuracy_fix

accuracy_fix.apply(accuracy)


def canonical_authority(slate: str, game_id: str, commence: str) -> dict:
    return {
        "version": "MLB-ROLLING-AUDIT-CANONICAL-LOCK-AUTHORITY-v1",
        "verified": True,
        "consistentRead": True,
        "sourcePk": f"GAME_WINNERS#mlb#{slate}",
        "sourceSk": f"LOCKED#GAME#{commence}#{game_id}",
        "recordType": "mlb_immutable_locked_single_game_prediction",
        "immutableLocked": True,
        "stageAuthorityVerified": True,
        "persistedStageAuthorityValidated": True,
        "exactLockVectorValidated": True,
        "exactProviderIdentityMatched": True,
        "matchMethod": "exact_provider_game_id_and_teams",
        "legacyOrDailyCardFallbackUsed": False,
    }


def main() -> int:
    # Display semantics retain every immutable locked winner. Official rolling
    # accuracy is a separate quality classification applied by the audit layer.
    locked = {
        "slatePredictionLock": {"locked": True, "lockStatus": "LOCKED"},
        "predictions": [
            {
                "gameId": "low-confidence-visible-lock",
                "predictedWinner": "Washington Nationals",
                "predictedSide": "home",
                "teamWinProbabilityPct": 59.99,
                "winProbabilityPct": 59.99,
                "actionablePick": False,
                "officialPick": False,
                "tags": ["SLATE_LOCKED", "ML_REJECTED", "NOT_PLAYABLE"],
            },
            {
                "gameId": "playable-visible-lock",
                "predictedWinner": "Los Angeles Dodgers",
                "actionablePick": True,
                "officialPick": False,
                "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            },
        ],
    }
    result = semantics.enhance_result(locked)
    first, second = result["predictions"]

    assert result["officialPredictionCount"] == 2
    assert result["officialPickCount"] == 2
    assert result["playablePredictionCount"] == 1
    assert result["nonPlayableOfficialPredictionCount"] == 1
    assert len(result["officialPredictionDisplay"]) == 2
    assert len(result["playablePredictionDisplay"]) == 1
    assert len(result["nonPlayableOfficialPredictionDisplay"]) == 1

    assert first["officialPrediction"] is True
    assert first["officialPick"] is True
    assert first["teamWinProbabilityPct"] == 59.99
    assert first["playable"] is False
    assert first["recommendationStatus"] == "OFFICIAL_PREDICTION_NOT_PLAYABLE"
    assert "OFFICIAL_LOCKED_PREDICTION" in first["tags"]
    assert "NOT_PLAYABLE" in first["tags"]

    assert second["officialPrediction"] is True
    assert second["officialPick"] is True
    assert second["playable"] is True
    assert second["recommendationStatus"] == "PLAYABLE_PREDICTION"

    pre_lock = semantics.enhance_result(
        {
            "slatePredictionLock": {"locked": False},
            "predictions": [{"gameId": "pre-lock", "predictedWinner": "New York Mets", "actionablePick": False}],
        }
    )
    row = pre_lock["predictions"][0]
    assert row["officialPrediction"] is False
    assert row["officialPick"] is False
    assert row["recommendationStatus"] == "PRE_LOCK_PREDICTION"

    audit_rows = [
        {
            "status": "GRADED",
            "id": "locked-diagnostic-below-floor",
            "slateDateEt": "2026-07-11",
            "commenceTime": "2026-07-11T18:00:00Z",
            "homeTeam": "Washington Nationals",
            "awayTeam": "New York Yankees",
            "winner": "New York Yankees",
            "predictedWinner": "New York Yankees",
            "predictedSide": "away",
            "correct": True,
            "officialPrediction": True,
            "officialPick": True,
            "actionablePick": False,
            "accuracyTargetEligible": False,
            "recommendationStatus": "OFFICIAL_PREDICTION_NOT_PLAYABLE",
            "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1",
            "tags": ["SLATE_LOCKED", "ML_REJECTED", "NOT_PLAYABLE"],
            "teamWinProbabilityPct": 58.0,
            "americanOdds": -125,
            "homeSignal": {"marketConsensusProbability": 0.42, "americanOdds": 115},
            "awaySignal": {"marketConsensusProbability": 0.58, "americanOdds": -125, "delta": 0.01, "reversalCount": 0},
            "canonicalLockAuthority": canonical_authority(
                "2026-07-11", "locked-diagnostic-below-floor", "2026-07-11T18:00:00Z"
            ),
        },
        {
            "status": "GRADED",
            "id": "official-quality-playable",
            "slateDateEt": "2026-07-11",
            "commenceTime": "2026-07-11T19:00:00Z",
            "homeTeam": "Los Angeles Dodgers",
            "awayTeam": "Arizona Diamondbacks",
            "winner": "Arizona Diamondbacks",
            "predictedWinner": "Los Angeles Dodgers",
            "predictedSide": "home",
            "correct": False,
            "officialPrediction": True,
            "officialPick": True,
            "actionablePick": True,
            "accuracyTargetEligible": True,
            "recommendationStatus": "PLAYABLE_PREDICTION",
            "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1",
            "tags": ["SLATE_LOCKED", "ACTIONABLE_PICK"],
            "teamWinProbabilityPct": 62.0,
            "americanOdds": -163,
            "homeSignal": {"marketConsensusProbability": 0.62, "americanOdds": -163, "delta": 0.01, "reversalCount": 0},
            "awaySignal": {"marketConsensusProbability": 0.38, "americanOdds": 145},
            "canonicalLockAuthority": canonical_authority(
                "2026-07-11", "official-quality-playable", "2026-07-11T19:00:00Z"
            ),
        },
    ]
    normalized = [accuracy._normalize_audit_row(row) for row in audit_rows]
    metrics = accuracy._window_metrics(normalized)

    assert normalized[0]["officialPrediction"] is False
    assert normalized[0]["playable"] is False
    assert normalized[0]["officialLockQualityGate"]["selectedTeamProbabilityPct"] == 58.0
    assert normalized[1]["officialPrediction"] is True
    assert normalized[1]["playable"] is True
    assert normalized[1]["officialLockQualityGate"]["officialEligible"] is True
    assert metrics["officialPredictions"]["count"] == 1
    assert metrics["playableRecommendations"]["count"] == 1
    assert metrics["officialPredictions"]["accuracyPct"] == 0.0
    assert metrics["playableRecommendations"]["accuracyPct"] == 0.0
    assert metrics["officialPredictions"]["probabilityScoring"]["brierScore"] is not None
    assert metrics["officialPredictions"]["probabilityScoring"]["logLoss"] is not None
    assert metrics["officialPredictions"]["roi"]["pricedPickCount"] == 1
    assert metrics["marketFavoriteBaseline"]["count"] == 1
    assert metrics["comparison"]["modelAccuracyLiftVsMarketPct"] is not None

    legacy = accuracy._normalize_audit_row(
        {
            "status": "GRADED",
            "id": "legacy-flipped-official-not-proven-playable",
            "slateDateEt": "2026-07-10",
            "commenceTime": "2026-07-10T20:00:00Z",
            "homeTeam": "Texas Rangers",
            "awayTeam": "Houston Astros",
            "winner": "Texas Rangers",
            "predictedWinner": "Houston Astros",
            "predictedSide": "away",
            "correct": False,
            "officialPick": True,
            "actionablePick": True,
            "accuracyTargetEligible": True,
            "actionability": "OPTIMIZED_GAME_WINNER_PICK",
            "tags": ["SLATE_LOCKED", "FAVORITE"],
            "winProbabilityPct": 6.66,
            "americanOdds": 120,
            "homeSignal": {"probLatest": 0.442592, "americanOdds": 120},
            "awaySignal": {"probLatest": 0.557408, "americanOdds": -142},
        }
    )
    assert legacy["officialPrediction"] is False
    assert legacy["playable"] is False
    assert legacy["teamWinProbabilityPct"] == 55.74
    assert legacy["lockedAmericanOdds"] == -142.0

    old_ledger_row = {
        "id": "old-ledger-no-status",
        "slateDateEt": "2026-07-10",
        "commenceTime": "2026-07-10T21:00:00Z",
        "homeTeam": "Baltimore Orioles",
        "awayTeam": "Kansas City Royals",
        "winner": "Baltimore Orioles",
        "predictedWinner": "Baltimore Orioles",
        "predictedSide": "home",
        "correct": True,
        "officialPrediction": True,
        "officialPick": True,
        "tags": ["SLATE_LOCKED"],
        "homeSignal": {"probLatest": 0.587345, "americanOdds": -156},
        "awaySignal": {"probLatest": 0.412655, "americanOdds": 132},
    }
    normalized_ledger = accuracy._normalize_audit_row(old_ledger_row)
    stored_ledger = accuracy._ledger_row(normalized_ledger)
    assert normalized_ledger["status"] == "GRADED"
    assert stored_ledger["status"] == "GRADED"
    assert accuracy._dedupe([normalized_ledger]) == []

    print("MLB visible-lock, 60% official-quality, playability, and canonical ledger semantics verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
