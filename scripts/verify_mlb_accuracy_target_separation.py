#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))

import mlb_accuracy_target_policy_v1 as policy


def _dual_model(
    selected_accuracy: float = 80.0,
    selected_count: int = 100,
    exact_odds_coverage: float = 80.0,
    calibration_error: float = 0.10,
    outcome_accuracy: float = 80.0,
):
    selected_threshold = {
        "ok": True,
        "threshold": 0.7,
        "selectedCount": selected_count,
        "coveragePct": 50.0,
        "accuracyPct": selected_accuracy,
        "selectionSource": "validation_only",
    }
    return {
        "ok": True,
        "status": "TRAINED",
        "outcomeModel": {"ok": True, "version": "test-outcome", "target": "homeWon"},
        "reliabilityModel": {
            "ok": True,
            "version": "test-reliability",
            "target": "pickCorrect",
            "selectedThreshold": selected_threshold,
            "thresholdSelectedOnValidationOnly": True,
        },
        "testWasUntouchedDuringFitAndThresholdSelection": True,
        "split": {"counts": {"train": 300, "validation": 100, "test": 100}},
        "validation": {"selectedReliability": selected_threshold},
        "dataQuality": {
            "modelScope": "MARKET_MOVEMENT_ONLY_WITH_MISSINGNESS",
            "averageFundamentalsCompletenessPct": 0.0,
        },
        "untouchedTest": {
            "outcome": {
                "count": 100,
                "accuracyPct": outcome_accuracy,
                "accuracyLiftPctPoints": 2.0,
                "brierSkillPct": 1.0,
                "logLoss": 0.60,
                "calibrationError": 0.05,
                "baseline": {"logLoss": 0.70},
            },
            "selectedReliability": {
                "count": selected_count,
                "accuracyPct": selected_accuracy,
                "exactOddsCoveragePct": exact_odds_coverage,
                "calibrationError": calibration_error,
            },
        },
    }


def main() -> int:
    installed = policy.install()
    assert installed.get("ok") is True, installed

    assert installed.get("rolling24hAllGamesAuditTargetPct") == 80.0
    assert installed.get("recommendationReliabilityThresholdPct") == 80.0
    assert installed.get("minimumOutcomeUntouchedAccuracyPct") == 80.0
    assert installed.get("selectedUntouchedTestPlayabilityAccuracyTargetPct") == 80.0
    assert installed.get("minimumExactOddsCoveragePct") == 80.0
    assert installed.get("minimumRolling24hSlateAccuracyPct") == 80.0
    assert installed.get("minimumIndividualGameLockProbabilityPct") == 60.0
    assert installed.get("minimumCleanOfficial") == 500
    assert installed.get("minimumUntouchedTest") == 100
    assert installed.get("minimumSelectedUntouchedTest") == 100
    assert installed.get("maximumReliabilityCalibrationError") == 0.10

    assert os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_OUTCOME_UNTOUCHED_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE") == "80.0"
    assert os.environ.get("INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ROLLING_24H_SLATE_AUTHORITY_TARGET_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_INDIVIDUAL_GAME_LOCK_MIN_PROBABILITY_PCT") == "60.0"

    import mlb_accuracy_target_patch as winner_target
    import mlb_individual_game_lock_probability_policy_v1 as lock_policy
    import mlb_ml_champion_challenger_v1 as champion
    import mlb_ml_runtime_safety_patch as runtime_safety
    import mlb_official_prediction_semantics as semantics
    import mlb_real_world_accuracy_semantics_fix as real_world
    import mlb_rolling_24h_audit as rolling_audit

    assert winner_target.ROLLING_TARGET_ACCURACY_PCT == 80.0
    assert rolling_audit.TARGET_ACCURACY_PCT == 80.0
    assert real_world.ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT == 80.0
    assert real_world.MIN_PLAYABLE_TARGET_ACCURACY_PCT == 80.0
    assert runtime_safety.MIN_ACCURACY_TARGET_PCT == 80.0
    assert runtime_safety.MIN_EXACT_ODDS_COVERAGE_PCT == 80.0
    assert runtime_safety.MIN_ROLLING_24H_SLATE_ACCURACY_PCT == 80.0
    assert champion.MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT == 80.0
    assert champion.MIN_SELECTED_RELIABILITY_ACCURACY == 80.0
    assert champion.MIN_SELECTED_PRICE_COVERAGE == 80.0
    assert champion.MIN_ROLLING_24H_SLATE_ACCURACY_PCT == 80.0
    assert lock_policy.MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT == 60.0

    locked = semantics.enhance_result(
        {
            "slatePredictionLock": {"locked": True},
            "predictions": [
                {
                    "gameId": "below-floor",
                    "predictedWinner": "Away Team",
                    "predictedSide": "away",
                    "teamWinProbabilityPct": 59.99,
                    "winProbabilityPct": 59.99,
                    "actionablePick": True,
                    "tags": ["FINAL_LOCKED", "ACTIONABLE_PICK"],
                },
                {
                    "gameId": "at-floor",
                    "predictedWinner": "Home Team",
                    "predictedSide": "home",
                    "teamWinProbabilityPct": 60.0,
                    "winProbabilityPct": 60.0,
                    "actionablePick": False,
                    "tags": ["FINAL_LOCKED"],
                },
            ],
        }
    )
    below, at_floor = locked["predictions"]
    assert below["officialPrediction"] is False
    assert below["officialPick"] is False
    assert below["actionablePick"] is False
    assert below["accuracyTargetEligible"] is False
    assert below["officialPredictionStatus"] == "LOCKED_DIAGNOSTIC_BELOW_60PCT"
    assert "BELOW_60PCT_GAME_LOCK_FLOOR" in below["tags"]
    assert at_floor["officialPrediction"] is True
    assert at_floor["officialPick"] is True
    assert at_floor["individualGameLockEligible"] is True
    assert locked["officialPredictionCount"] == 1

    overlay = SimpleNamespace()
    runtime_safety.apply(overlay)
    runtime_model = {
        "productionApproved": True,
        "promotionTargetAccuracyPct": 80.0,
        "testMetrics": {
            "testCount": 100,
            "selectedCount": 100,
            "selectedAccuracyPct": 80.0,
            "exactOddsCoveragePct": 80.0,
            "selectedCalibrationError": 0.10,
            "rolling24hSlateAccuracyPct": 80.0,
        },
    }
    assert overlay._validated(runtime_model, {}, 80.0) is True
    assert overlay._validated(
        {**runtime_model, "testMetrics": {**runtime_model["testMetrics"], "selectedAccuracyPct": 79.99}},
        {},
        80.0,
    ) is False
    assert overlay._validated(
        {**runtime_model, "testMetrics": {**runtime_model["testMetrics"], "exactOddsCoveragePct": 79.99}},
        {},
        80.0,
    ) is False

    champion.AUTO_PROMOTE = True
    at_threshold = champion.evaluate(
        _dual_model(),
        clean_count=500,
        playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert at_threshold.get("directionPromotionEligible") is True, at_threshold
    assert at_threshold.get("playabilityPromotionEligible") is True, at_threshold
    assert at_threshold.get("promotionDecision") == "PROMOTE"
    assert at_threshold.get("recommendationReliabilityThresholdPct") == 80.0
    assert at_threshold.get("minimumOutcomeUntouchedAccuracyPct") == 80.0
    assert at_threshold.get("minimumSelectedExactOddsCoveragePct") == 80.0
    assert at_threshold.get("minimumIndividualGameLockProbabilityPct") == 60.0

    below_outcome = champion.evaluate(
        _dual_model(outcome_accuracy=79.99),
        clean_count=500,
        playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert below_outcome.get("directionPromotionEligible") is False
    assert "OUTCOME_UNTOUCHED_ACCURACY_BELOW_AUTHORITY_TARGET" in {
        item.get("code") for item in below_outcome.get("directionBlockers") or []
    }

    below_reliability = champion.evaluate(
        _dual_model(selected_accuracy=79.99),
        clean_count=500,
        playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert below_reliability.get("playabilityPromotionEligible") is False
    assert "SELECTED_ACCURACY_TOO_LOW" in {
        item.get("code") for item in below_reliability.get("playabilityBlockers") or []
    }

    print(
        "MLB targets verified: 80% rolling, outcome, playable reliability, and exact locked-odds coverage; "
        "60% selected-team probability required for an official individual-game lock."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
