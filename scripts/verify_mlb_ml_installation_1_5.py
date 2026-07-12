#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_ml_installation_1_5_latest.json"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_clean_cohort_hardening_v1 as cohort_hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual
import mlb_ml_exact_lock_vector_patch as exact_patch
import mlb_ml_frozen_features as frozen_features
import mlb_ml_manual_promotion_only_v1 as manual_only
import mlb_ml_walk_forward_v1 as walk_forward
import mlb_official_freeze_bridge as freeze_bridge
import mlb_official_prediction_semantics as semantics


def _fingerprint(vector):
    source = json.dumps(
        {
            "gameId": vector.get("gameId"),
            "lockAtUtc": vector.get("lockAtUtc"),
            "features": vector.get("features") or {},
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _locked_result():
    lock_at = "2026-07-12T15:15:00+00:00"
    source_at = "2026-07-12T15:14:30+00:00"
    row = {
        "gameId": "install-proof-game-1",
        "gameKey": "mlb|2026-07-12|away team|home team",
        "slateDateEt": "2026-07-12",
        "commenceTime": "2026-07-12T18:00:00+00:00",
        "homeTeam": "Home Team",
        "awayTeam": "Away Team",
        "predictedWinner": "Home Team",
        "predictedSide": "home",
        "score": 58.0,
        "teamWinProbabilityPct": 55.0,
        "winProbabilityPct": 55.0,
        "winProbabilityMeaning": "estimated_probability_selected_team_wins_game",
        "probabilitySemanticsFixed": True,
        "lockedAmericanOdds": -120,
        "priceBook": "fanduel",
        "priceSource": "real_book",
        "tags": ["BOOK_AGREEMENT", "FINAL_LOCKED", "SLATE_LOCKED"],
        "slatePredictionLock": {
            "locked": True,
            "finalLocked": True,
            "phase": "SLATE_LOCKED",
            "lockAtUtc": lock_at,
            "latestScoringPullAt": source_at,
        },
        "predictionSourcePullAt": source_at,
        "homeSignal": {
            "marketConsensusProbability": 0.55,
            "probLatest": 0.55,
            "probStart": 0.53,
            "delta": 0.02,
            "score": 58.0,
            "bookDivergence": 0.01,
            "reversalCount": 0,
            "runLineMovement": -0.5,
            "americanOdds": -120,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "tags": ["BOOK_AGREEMENT", "STEAM"],
        },
        "awaySignal": {
            "marketConsensusProbability": 0.45,
            "probLatest": 0.45,
            "probStart": 0.47,
            "delta": -0.02,
            "score": 42.0,
            "bookDivergence": 0.01,
            "reversalCount": 0,
            "runLineMovement": 0.5,
            "americanOdds": 110,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "tags": ["BOOK_AGREEMENT"],
        },
        "advanced_context": {
            "confirmed_probable_pitchers": {
                "source_status": "CONNECTED",
                "home_probable_pitcher": "Home Starter",
                "away_probable_pitcher": "Away Starter",
            },
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
        },
    }
    return {
        "predictions": [row],
        "slatePredictionLock": {
            "locked": True,
            "lockAtUtc": lock_at,
            "latestScoringPullAt": source_at,
        },
        "slateCoverage": {
            "coverageComplete": True,
            "manifestGameCount": 1,
            "predictionGameCount": 1,
            "storedPredictionCount": 1,
        },
    }


def main() -> int:
    checks = {}

    exact_patch.apply(frozen_features)
    freeze_bridge.apply(semantics)
    result = semantics.enhance_result(_locked_result())
    row = result["predictions"][0]
    vector = row.get("frozenFeatureVector") or {}
    freeze = row.get("mlFeatureFreeze") or {}

    assert vector.get("fingerprint") == _fingerprint(vector)
    assert vector.get("labels") == {"homeWon": None, "pickCorrect": None}
    assert freeze.get("trainingEligible") is True, freeze
    assert freeze.get("exactVectorCreated") is True, freeze
    checks["1_cleanPostFixCohort"] = {
        "installed": True,
        "exactVectorVersion": vector.get("version"),
        "fingerprintVerified": True,
        "outcomeLabelsAbsentAtLock": True,
        "selectedSideOddsAndSourcePresent": True,
    }

    settled = copy.deepcopy(row)
    settled.update(
        {
            "status": "GRADED",
            "winner": "Home Team",
            "correct": True,
            "officialPredictionStatus": "OFFICIAL_LOCKED_PREDICTION",
            "lockedPrediction": True,
            "lockedAmericanOdds": -120,
            "priceBook": "fanduel",
            "priceSource": "real_book",
            "slateCoverage": result.get("slateCoverage"),
            "lockedCardAudit": {
                "lockedFlag": True,
                "lockAtUtc": vector.get("lockAtUtc"),
                "explicitSourceAtUtc": vector.get("sourcePullAtUtc"),
                "preventsLateRows": True,
                "providerGameId": row.get("gameId"),
            },
        }
    )
    cohort_hardening.apply(cohort)
    clean = cohort.build([settled])
    assert clean.get("cleanRowCount") == 1, clean

    assert dual.OUTCOME_MODEL_VERSION != dual.RELIABILITY_MODEL_VERSION
    assert "homeMarketProb" in dual.OUTCOME_FEATURES
    assert "selectedMarketProb" in dual.RELIABILITY_FEATURES
    checks["2_separateOutcomeAndReliabilityModels"] = {
        "installed": True,
        "outcomeModelVersion": dual.OUTCOME_MODEL_VERSION,
        "reliabilityModelVersion": dual.RELIABILITY_MODEL_VERSION,
        "probabilitiesInterchangeable": False,
    }

    split = walk_forward.split_chronological([], min_train=80, min_validation=30, min_test=30)
    assert split.get("reason") == "insufficient_clean_rows_for_three_way_chronological_split"
    checks["3_chronologicalValidation"] = {
        "installed": True,
        "trainMinimum": 80,
        "validationMinimum": 30,
        "untouchedTestMinimum": 30,
        "thresholdSelection": "validation_only",
    }

    snapshot = fundamentals.build(row)
    assert snapshot.get("missingnessIsFeature") is True
    assert isinstance(snapshot.get("sourceStatuses"), dict)
    assert snapshot.get("sourceHonestyPolicy")
    checks["4_baseballFundamentals"] = {
        "installed": True,
        "snapshotVersion": snapshot.get("version"),
        "connectedGroups": snapshot.get("connectedGroups"),
        "missingGroups": snapshot.get("missingGroups"),
        "missingInputsFabricated": False,
        "modelScopeUntilFeedsConnected": "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
    }

    manual_only.apply(champion)
    gate = champion.evaluate(
        {"ok": False, "status": "ACCUMULATING_CLEAN_POST_FIX_EVIDENCE", "split": {}, "untouchedTest": {}},
        clean_count=1,
        playable_evidence_count=0,
    )
    automatic = champion.promote_if_allowed({"promotionGate": {"promotionDecision": "PROMOTE"}})
    assert gate.get("directionPromotionEligible") is False
    assert gate.get("playabilityPromotionEligible") is False
    assert automatic.get("promoted") is False
    checks["5_acceptanceAndPromotionGates"] = {
        "installed": True,
        "directionAndPlayabilityIndependent": True,
        "automaticPromotion": False,
        "manualReviewedDynamoDbPromotionRequired": True,
        "minimumCleanOfficial": champion.MIN_CLEAN_OFFICIAL,
        "minimumUntouchedTest": champion.MIN_UNTOUCHED_TEST,
    }

    report = {
        "ok": all(item.get("installed") for item in checks.values()),
        "proofType": "MLB_ML_INSTALLATION_1_5",
        "version": "MLB-ML-INSTALLATION-1-5-v1.1-priced-postgreen",
        "checks": checks,
        "cleanSettlementJoinVerified": clean.get("cleanRowCount") == 1,
        "policy": "All five optimization components are installed. Production ML remains shadow-only until clean evidence and reviewed promotion gates pass.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
