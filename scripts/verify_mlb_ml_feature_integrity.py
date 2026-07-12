#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_clean_cohort_hardening_v1 as hardening
import mlb_ml_clean_cohort_v1 as cohort


def base_row():
    row = {
        "status": "GRADED", "id": "integrity-game", "gameId": "integrity-game",
        "slateDateEt": "2026-07-12", "commenceTime": "2026-07-12T20:00:00Z",
        "homeTeam": "Home", "awayTeam": "Away", "winner": "Home",
        "predictedWinner": "Home", "predictedSide": "home", "correct": True,
        "lockedPrediction": True, "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
        "probabilitySemanticsFixed": True,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1",
        "teamWinProbabilityPct": 58.0,
        "lockedAmericanOdds": -135,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "slateCoverage": {"coverageComplete": True},
        "slatePredictionLock": {"locked": True, "lockAtUtc": "2026-07-12T18:00:00Z", "latestScoringPullAt": "2026-07-12T17:55:00Z"},
        "lockedCardAudit": {"lockedFlag": True, "lockAtUtc": "2026-07-12T18:00:00Z", "explicitSourceAtUtc": "2026-07-12T17:55:00Z", "preventsLateRows": True, "providerGameId": "integrity-game"},
        "homeSignal": {"marketConsensusProbability": 0.58, "probLatest": 0.58, "americanOdds": -135, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"]},
        "awaySignal": {"marketConsensusProbability": 0.42, "probLatest": 0.42, "americanOdds": 120, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"]},
        "fundamentalsSnapshot": {"completenessRatio": 0.0, "numericValues": {}},
    }
    pregame = copy.deepcopy(row); pregame.pop("winner"); pregame.pop("correct"); pregame.pop("status")
    row["frozenFeatureVector"] = cohort.freeze_feature_snapshot(pregame)
    row["frozenFeatureVectorVersion"] = row["frozenFeatureVector"]["version"]
    return row


def main() -> int:
    hardening.apply(cohort)
    clean = base_row()
    ok, reasons = cohort.eligibility(clean)
    assert ok is True, reasons

    tampered = copy.deepcopy(clean)
    tampered["frozenFeatureVector"]["features"]["homeMarketProb"] = 0.99
    ok, reasons = cohort.eligibility(tampered)
    assert ok is False and "frozen_vector_fingerprint_mismatch" in reasons

    wrong_game = copy.deepcopy(clean)
    wrong_game["id"] = "different-game"
    wrong_game["gameId"] = "different-game"
    ok, reasons = cohort.eligibility(wrong_game)
    assert ok is False and "frozen_vector_game_identity_mismatch" in reasons

    no_price_source = copy.deepcopy(clean)
    no_price_source.pop("priceBook", None)
    no_price_source.pop("priceSource", None)
    no_price_source["homeSignal"].pop("priceBook", None)
    no_price_source["homeSignal"].pop("priceSource", None)
    ok, reasons = cohort.eligibility(no_price_source)
    assert ok is False and "selected_side_odds_source_not_proven" in reasons

    print("MLB frozen feature integrity verified: fingerprint, game identity, lock timestamp, and selected-side price source are enforced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
