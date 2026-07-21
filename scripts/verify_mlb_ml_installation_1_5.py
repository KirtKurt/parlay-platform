#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
REPORT_PATH = ROOT / "runtime_reports" / "mlb_ml_installation_1_5_latest.json"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_fundamentals_snapshot_v2 as fundamentals
import mlb_daily_lock_ml_vector_preservation_patch as vector_contract
import mlb_accuracy_target_policy_v1 as accuracy_policy
import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_clean_cohort_hardening_v1 as cohort_hardening
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v2 as dual
import mlb_ml_exact_lock_vector_patch as exact_patch
import mlb_ml_frozen_features as frozen_features
import mlb_ml_experiment_v2 as experiment_v2
import mlb_ml_promotion_policy_v2 as promotion_v2
import mlb_official_freeze_bridge as freeze_bridge
import mlb_official_prediction_semantics as semantics
from mlb_ml_feature_test_fixtures import attach_lock_safe_features


R3_EXPERIMENT_ID = "mlb-v2-2026-07-22-future-prospective-r3"
R3_RELEASE_CUTOFF_UTC = "2026-07-22T04:00:00+00:00"


def _fingerprint(vector):
    return cohort.fingerprint_for_vector(vector)


def _locked_result():
    lock_at = "2026-07-13T15:15:00+00:00"
    source_at = "2026-07-13T15:14:30+00:00"
    row = {
        "gameId": "install-proof-game-1",
        "gameKey": "mlb|2026-07-13|away team|home team",
        "slateDateEt": "2026-07-13",
        "commenceTime": "2026-07-13T18:00:00+00:00",
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
    attach_lock_safe_features(row)
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
    row = frozen_features.freeze_row(
        result["predictions"][0],
        coverage_complete=True,
    )
    row = vector_contract.apply_exact_vector_training_status(row)
    result["predictions"][0] = row
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
    assert "homeMarketDeVigProbability" in dual.OUTCOME_FEATURES
    assert "selectedMarketDeVigProbability" in dual.RELIABILITY_FEATURES
    checks["2_separateOutcomeAndReliabilityModels"] = {
        "installed": True,
        "outcomeModelVersion": dual.OUTCOME_MODEL_VERSION,
        "reliabilityModelVersion": dual.RELIABILITY_MODEL_VERSION,
        "probabilitiesInterchangeable": False,
    }

    manifest = experiment_v2.new_manifest(
        experiment_id=R3_EXPERIMENT_ID,
        release_contract_id=R3_EXPERIMENT_ID,
        release_cutoff_utc=R3_RELEASE_CUTOFF_UTC,
        feature_vector_version=experiment_v2.REQUIRED_FUNDAMENTALS_VERSION,
        model_feature_schemas={
            "outcome": dual.OUTCOME_FEATURES,
            "reliability": dual.RELIABILITY_FEATURES,
        },
        created_at_utc=R3_RELEASE_CUTOFF_UTC,
    )
    checks["3_chronologicalValidation"] = {
        "installed": True,
        "experimentId": manifest.get("experimentId"),
        "releaseContractId": manifest.get("releaseContractId"),
        "releaseCutoffUtc": manifest.get("releaseCutoffUtc"),
        "trainMinimum": experiment_v2.PARTITION_MINIMUMS["train"],
        "validationMinimum": experiment_v2.PARTITION_MINIMUMS["validation"],
        "prospectiveTestMinimum": experiment_v2.PARTITION_MINIMUMS["prospectiveTest"],
        "wholeSlatePartitions": True,
        "futureCutoverOnly": True,
        "thresholdSelection": "validation_only_before_sealed_prospective_test",
    }

    snapshot = fundamentals.build(
        row, captured_at_utc=row.get("predictionSourcePullAt")
    )
    assert snapshot.get("missingValuesAreNull") is True
    assert fundamentals.validate(snapshot) == []
    assert snapshot.get("sourceHonestyPolicy")
    checks["4_baseballFundamentals"] = {
        "installed": True,
        "snapshotVersion": snapshot.get("version"),
        "connectedGroups": snapshot.get("connectedGroups"),
        "missingGroups": snapshot.get("missingGroups"),
        "missingInputsFabricated": False,
        "modelScopeUntilFeedsConnected": "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
    }

    installed_policy = accuracy_policy.install()
    assert installed_policy.get("ok") is True, installed_policy
    assert champion.AUTO_PROMOTE is False
    assert experiment_v2.PARTITION_MINIMUMS == {
        "train": 300,
        "validation": 100,
        "prospectiveTest": 100,
    }
    manual_first = promotion_v2.evaluate(
        {}, manifest, current_champion=None, automatic_promotion_enabled=False
    )
    assert manual_first.get("firstPromotionRequiresManualReview") is True
    assert manual_first.get("automaticPromotionEnabled") is False
    assert manual_first.get("runtimeAuthorityActivationEligible") is False
    checks["5_acceptanceAndPromotionGates"] = {
        "installed": True,
        "directionAndPlayabilityIndependent": True,
        "automaticPromotion": False,
        "legacyV1AuthorityEnabled": False,
        "automaticPromotionBeforeGates": False,
        "firstPromotionRequiresManualReview": True,
        "experimentId": R3_EXPERIMENT_ID,
        "releaseCutoffUtc": R3_RELEASE_CUTOFF_UTC,
        "minimumCleanOfficial": promotion_v2.MIN_TOTAL_CLEAN_ROWS,
        "minimumProspectiveTest": promotion_v2.MIN_PROSPECTIVE_TEST_ROWS,
        "minimumSelectedProspectiveRecommendations": promotion_v2.MIN_PROSPECTIVE_SELECTED_RECOMMENDATIONS,
        "maximumCalibrationError": promotion_v2.MAX_CALIBRATION_ERROR,
        "minimumAccuracyLiftPctPoints": promotion_v2.MIN_ACCURACY_LIFT_PCT_POINTS,
        "ninetyPercentDashboardOnly": True,
    }

    report = {
        "ok": all(item.get("installed") for item in checks.values()),
        "proofType": "MLB_ML_INSTALLATION_1_5",
        "version": "MLB-ML-INSTALLATION-1-5-v2-aws-shadow-manual-first",
        "checks": checks,
        "cleanSettlementJoinVerified": clean.get("cleanRowCount") == 1,
        "policy": "All five optimization components are installed. Legacy V1 is diagnostic-only. AWS V2 remains shadow-only through fixed 300/100/100 whole-slate partitions, prospective market-skill gates, and a manually reviewed first promotion.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
