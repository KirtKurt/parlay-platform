#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_champion_runtime_v1 as runtime
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual


def row(index: int, modern: bool = True):
    home_probability = 0.42 + (index % 17) * 0.01
    home_probability = min(0.68, home_probability)
    home_won = 1 if (home_probability >= 0.52 and index % 5 != 0) or index % 11 == 0 else 0
    predicted_side = "home" if home_probability >= 0.5 else "away"
    predicted_winner = "Home Team" if predicted_side == "home" else "Away Team"
    winner = "Home Team" if home_won else "Away Team"
    lock_hour = 15 + (index // 60)
    minute = index % 60
    lock_at = f"2026-07-12T{lock_hour:02d}:{minute:02d}:00+00:00"
    source_at = f"2026-07-12T{lock_hour:02d}:{max(0, minute - 1):02d}:00+00:00"
    return {
        "status": "GRADED",
        "id": f"game-{index}",
        "slateDateEt": "2026-07-12",
        "commenceTime": f"2026-07-12T{18 + (index // 60):02d}:{minute:02d}:00Z",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "winner": winner,
        "predictedWinner": predicted_winner,
        "predictedSide": predicted_side,
        "correct": predicted_winner == winner,
        "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "lockedPrediction": True,
        "probabilitySemanticsFixed": modern,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1" if modern else None,
        "teamWinProbabilityPct": round(max(home_probability, 1.0 - home_probability) * 100.0, 2) if modern else 7.5,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game" if modern else "legacy_reliability",
        "score": 50 + (home_probability - 0.5) * 100,
        "lockedCardAudit": {
            "lockedFlag": True,
            "lockAtUtc": lock_at,
            "explicitSourceAtUtc": source_at,
            "preventsLateRows": True,
            "version": "MLB-LOCKED-CARD-AUDIT-v2-doubleheader-safe-provider-time-match",
        },
        "homeSignal": {
            "marketConsensusProbability": home_probability,
            "probLatest": home_probability,
            "delta": (index % 7 - 3) / 1000.0,
            "bookDivergence": 0.01 + (index % 4) / 1000.0,
            "reversalCount": index % 3,
            "runLineMovement": index % 5,
            "americanOdds": -110 if home_probability >= 0.5 else 120,
            "tags": ["BOOK_AGREEMENT"] + (["STEAM"] if index % 4 == 0 else []),
        },
        "awaySignal": {
            "marketConsensusProbability": 1.0 - home_probability,
            "probLatest": 1.0 - home_probability,
            "delta": -(index % 7 - 3) / 1000.0,
            "bookDivergence": 0.01 + (index % 4) / 1000.0,
            "reversalCount": (index + 1) % 3,
            "runLineMovement": -(index % 5),
            "americanOdds": 100 if home_probability >= 0.5 else -120,
            "tags": ["BOOK_AGREEMENT"],
        },
    }


def main() -> int:
    legacy = row(1, modern=False)
    modern = row(2, modern=True)
    ok, reasons = cohort.eligibility(legacy)
    assert ok is False and "legacy_probability_semantics" in reasons
    ok, reasons = cohort.eligibility(modern)
    assert ok is True, reasons

    snapshot = fundamentals.build(modern)
    assert snapshot["missingnessIsFeature"] is True
    assert "sourceStatuses" in snapshot
    modern["fundamentalsSnapshot"] = snapshot
    frozen = cohort.freeze_feature_snapshot(modern)
    assert frozen["labels"]["homeWon"] in {0, 1}
    assert frozen["labels"]["pickCorrect"] in {0, 1}
    assert frozen["features"]["homeMarketProb"] != frozen["features"]["awayMarketProb"]

    rows = [row(index, modern=True) for index in range(180)]
    built = cohort.build([legacy, *rows])
    assert built["cleanRowCount"] == 180
    assert built["quarantinedRowCount"] == 1

    trained = dual.train(built["cleanRows"])
    assert trained["ok"] is True, trained
    assert trained["outcomeModel"]["target"] == "homeWon"
    assert trained["reliabilityModel"]["target"] == "pickCorrect"
    assert trained["testWasUntouchedDuringFitAndThresholdSelection"] is True
    assert trained["reliabilityModel"]["thresholdSelectedOnValidationOnly"] is True
    assert trained["split"]["counts"]["test"] >= 30

    gate = champion.evaluate(trained, clean_count=180, playable_evidence_count=20)
    assert gate["promotionEligible"] is False
    blocker_codes = {item["code"] for item in gate["blockers"]}
    assert "INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE" in blocker_codes
    assert "INSUFFICIENT_PLAYABLE_EVIDENCE" in blocker_codes
    assert gate["directionAuthorityEnabled"] is False

    original_loader = runtime.champion_store.load_champion
    runtime.champion_store.load_champion = lambda: None
    try:
        pregame = row(181, modern=True)
        pregame.pop("winner", None)
        pregame.pop("correct", None)
        original_winner = pregame["predictedWinner"]
        result = runtime.enhance_result({"predictions": [pregame]})
        scored = result["predictions"][0]
        assert scored["predictedWinner"] == original_winner
        assert scored["mlOptimizationShadowOnly"] is True
        assert result["mlOptimizationRuntime"]["shadowOnly"] is True
    finally:
        runtime.champion_store.load_champion = original_loader

    print("MLB ML optimization v3 clean cohort, dual models, untouched test, fundamentals, and promotion gates verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
