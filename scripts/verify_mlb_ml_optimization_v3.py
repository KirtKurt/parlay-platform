#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_champion_runtime_v1 as runtime
import mlb_ml_clean_cohort_hardening_v1 as cohort_hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual
import mlb_ml_frozen_features as canonical_freeze
from mlb_ml_feature_test_fixtures import attach_lock_safe_features

cohort_hardening.apply(cohort)


def source_honest_context():
    return {
        "version": "TEST-CONTEXT-v1",
        "confirmed_probable_pitchers": {"source_status": "PARTIAL", "home_probable_pitcher": "Home Starter", "away_probable_pitcher": "Away Starter"},
        "fip_xfip": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "wrc_plus": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "starter_handedness_splits": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "bullpen_fatigue": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "confirmed_lineups": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "weather_wind_roof": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "ballpark_factors": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "injuries_late_scratches_news": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "public_betting_handle": {"source_status": "NOT_CONNECTED_SOURCE_REQUIRED"},
        "closing_line_value": {"source_status": "SCHEMA_CONNECTED_PENDING_CLOSING_SNAPSHOT"},
    }


def row(index: int, modern: bool = True):
    home_probability = min(0.68, 0.42 + (index % 17) * 0.01)
    home_won = 1 if (home_probability >= 0.52 and index % 5 != 0) or index % 11 == 0 else 0
    predicted_side = "home" if home_probability >= 0.5 else "away"
    predicted_winner = "Home Team" if predicted_side == "home" else "Away Team"
    winner = "Home Team" if home_won else "Away Team"
    lock_hour = 15 + (index // 60); minute = index % 60
    lock_at = f"2026-07-13T{lock_hour:02d}:{minute:02d}:00+00:00"
    source_at = f"2026-07-13T{lock_hour:02d}:{max(0, minute - 1):02d}:00+00:00"
    selected_price = -110 if predicted_side == "home" else (100 if home_probability >= 0.5 else -120)
    return {
        "status": "GRADED", "id": f"game-{index}", "gameId": f"game-{index}",
        "slateDateEt": "2026-07-13", "commenceTime": f"2026-07-13T{18 + (index // 60):02d}:{minute:02d}:00Z",
        "homeTeam": "Home Team", "awayTeam": "Away Team", "winner": winner,
        "predictedWinner": predicted_winner, "predictedSide": predicted_side,
        "correct": predicted_winner == winner, "officialPrediction": True,
        "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION", "lockedPrediction": True,
        "probabilitySemanticsFixed": modern,
        "predictionSemanticsVersion": "MLB-OFFICIAL-PREDICTION-SEMANTICS-v1" if modern else None,
        "teamWinProbabilityPct": round(max(home_probability, 1.0 - home_probability) * 100.0, 2) if modern else 7.5,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game" if modern else "legacy_reliability",
        "score": 50 + (home_probability - 0.5) * 100,
        "lockedAmericanOdds": selected_price,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "advanced_context": source_honest_context(),
        "slateCoverage": {"coverageComplete": True, "manifestGameCount": 1, "predictionGameCount": 1, "storedPredictionCount": 1},
        "slatePredictionLock": {"locked": True, "finalLocked": True, "phase": "SLATE_LOCKED", "lockAtUtc": lock_at, "latestScoringPullAt": source_at},
        "lockedCardAudit": {"lockedFlag": True, "lockAtUtc": lock_at, "explicitSourceAtUtc": source_at, "preventsLateRows": True, "version": "MLB-LOCKED-CARD-AUDIT-v3-provider-alias-nearest-time-doubleheader-safe"},
        "homeSignal": {"marketConsensusProbability": home_probability, "probLatest": home_probability, "delta": (index % 7 - 3) / 1000.0, "bookDivergence": 0.01 + (index % 4) / 1000.0, "reversalCount": index % 3, "runLineMovement": index % 5, "americanOdds": -110 if home_probability >= 0.5 else 120, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"] + (["STEAM"] if index % 4 == 0 else [])},
        "awaySignal": {"marketConsensusProbability": 1.0 - home_probability, "probLatest": 1.0 - home_probability, "delta": -(index % 7 - 3) / 1000.0, "bookDivergence": 0.01 + (index % 4) / 1000.0, "reversalCount": (index + 1) % 3, "runLineMovement": -(index % 5), "americanOdds": 100 if home_probability >= 0.5 else -120, "priceBook": "fanduel", "priceSource": "real_book", "tags": ["BOOK_AGREEMENT"]},
    }


def frozen_row(index: int, modern: bool = True):
    base = row(index, modern=modern)
    final = {"status": base.pop("status"), "winner": base.pop("winner"), "correct": base.pop("correct")}
    attach_lock_safe_features(base)
    frozen = canonical_freeze.freeze_row(base, coverage_complete=True)
    frozen["frozenFeatureVector"] = cohort.freeze_feature_snapshot(frozen)
    frozen["frozenFeatureVectorVersion"] = frozen["frozenFeatureVector"].get("version")
    assert frozen["frozenFeatureVector"]["labels"]["homeWon"] is None
    assert frozen["frozenFeatureVector"]["labels"]["pickCorrect"] is None
    frozen.update(final)
    return frozen


def main() -> int:
    legacy = frozen_row(180, modern=False); modern = frozen_row(2, modern=True)
    ok, reasons = cohort.eligibility(legacy)
    assert ok is False and "legacy_probability_semantics" in reasons
    ok, reasons = cohort.eligibility(modern)
    assert ok is True, reasons
    snapshot = modern["fundamentalsSnapshot"]
    assert snapshot["missingnessIsFeature"] is True and "sourceStatuses" in snapshot
    frozen = modern["frozenFeatureVector"]
    assert frozen["labels"]["homeWon"] is None and frozen["labels"]["pickCorrect"] is None
    assert frozen["features"]["homeMarketProb"] != frozen["features"]["awayMarketProb"]
    assert modern["mlFeatureFreeze"]["trainingEligible"] is True
    normalized_single = cohort.build([modern])["cleanRows"]
    joined = dual.records_from_clean_rows(normalized_single)
    assert len(joined) == 1
    assert joined[0]["homeWon"] in {0, 1} and joined[0]["pickCorrect"] in {0, 1}
    assert joined[0]["labelSource"] == "final_settlement_join_not_pregame_feature_vector"

    rows = [frozen_row(index, modern=True) for index in range(180)]
    built = cohort.build([legacy, *rows])
    assert built["cleanRowCount"] == 180 and built["quarantinedRowCount"] == 1, built
    assert built["completeSlateCoverageRequired"] is True and built["immutableFrozenFeatureVectorRequired"] is True
    assert built["selectedSideLockedOddsRequired"] is True
    assert built["selectedSideOddsBookOrRealSourceRequired"] is True
    assert built["frozenVectorOutcomeLabelsMustRemainBlank"] is True
    trained = dual.train(built["cleanRows"])
    assert trained["ok"] is True, trained
    assert trained["featureLabelPolicy"] == "immutable_pregame_features_plus_final_settlement_labels"
    assert trained["outcomeModel"]["target"] == "homeWon" and trained["reliabilityModel"]["target"] == "pickCorrect"
    assert trained["testWasUntouchedDuringFitAndThresholdSelection"] is True
    assert trained["reliabilityModel"]["thresholdSelectedOnValidationOnly"] is True
    assert trained["split"]["counts"]["test"] >= 30
    selected_test = trained["untouchedTest"]["selectedReliability"]
    assert "priceCoveragePct" in selected_test and "exactOddsCoveragePct" in selected_test
    assert trained["dataQuality"]["modelScope"] == "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS"

    gate = champion.evaluate(trained, clean_count=180, playable_evidence_count=20)
    assert gate["promotionEligible"] is False
    assert "INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE" in {item["code"] for item in gate["directionBlockers"]}
    assert "INSUFFICIENT_CLEAN_OFFICIAL_EVIDENCE" in {item["code"] for item in gate["playabilityBlockers"]}
    assert gate["publicPlayableClaim"]["eligible"] is False
    assert gate["directionAuthorityEnabled"] is False and gate["playabilityAuthorityEnabled"] is False

    original_loader = runtime.champion_store.load_champion; runtime.champion_store.load_champion = lambda: None
    try:
        pregame = row(181, modern=True); pregame.pop("winner", None); pregame.pop("correct", None)
        original_winner = pregame["predictedWinner"]
        result = runtime.enhance_result({"predictions": [pregame]}); scored = result["predictions"][0]
        assert scored["predictedWinner"] == original_winner
        assert scored["mlOptimizationShadowOnly"] is True and result["mlOptimizationRuntime"]["shadowOnly"] is True
    finally:
        runtime.champion_store.load_champion = original_loader

    print("MLB ML optimization v3 verified: immutable pregame features, final-label join, dual models, exact-odds validation, independent 90% authority gates, and shadow-only behavior below target")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
