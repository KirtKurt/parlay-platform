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
    selected_accuracy: float = 90.0,
    selected_count: int = 100,
    exact_odds_coverage: float = 90.0,
    calibration_error: float = 0.10,
    outcome_accuracy: float = 90.0,
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
    assert installed.get("recommendationReliabilityThresholdPct") == 90.0
    assert installed.get("selectedUntouchedTestPlayabilityAccuracyTargetPct") == 90.0
    assert installed.get("minimumCleanOfficial") == 500
    assert installed.get("minimumUntouchedTest") == 100
    assert installed.get("minimumSelectedUntouchedTest") == 100
    assert installed.get("minimumExactOddsCoveragePct") == 90.0
    assert installed.get("maximumReliabilityCalibrationError") == 0.10
    assert installed.get("minimumRolling24hSlateAccuracyPct") == 80.0
    assert installed.get("rolling24hSlateAccuracyProgressMilestonesPct") == [50.0, 60.0, 70.0, 80.0]
    assert installed.get("rolling24hSlateAccuracyProgressMilestonesReportingOnly") is True

    assert os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY") == "90.0"
    assert os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY") == "90.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY") == "90.0"
    assert os.environ.get("INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST") == "100"
    assert os.environ.get("INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS") == "100"
    assert os.environ.get("INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY") == "80.0"
    assert os.environ.get("INQSI_MLB_ROLLING_24H_SLATE_AUTHORITY_TARGET_ACCURACY") == "80.0"

    import mlb_accuracy_target_patch as winner_target
    import mlb_ml_runtime_safety_patch as runtime_safety
    import mlb_ml_champion_challenger_v1 as champion
    import mlb_ml_optimization_v3 as optimization
    import mlb_real_world_accuracy_semantics_fix as semantics
    import mlb_rolling_24h_audit as rolling_audit

    assert winner_target.ROLLING_TARGET_ACCURACY_PCT == 80.0
    assert rolling_audit.TARGET_ACCURACY_PCT == 80.0
    assert runtime_safety.MIN_ACCURACY_TARGET_PCT == 90.0
    assert runtime_safety.MIN_ROLLING_24H_SLATE_ACCURACY_PCT == 80.0
    assert runtime_safety.MIN_PRODUCTION_TEST_ROWS == 100
    assert runtime_safety.MIN_PRODUCTION_SELECTED_TEST_ROWS == 100
    assert runtime_safety.MIN_EXACT_ODDS_COVERAGE_PCT == 90.0
    assert runtime_safety.MAX_RELIABILITY_CALIBRATION_ERROR == 0.10
    assert champion.MIN_SELECTED_RELIABILITY_ACCURACY == 90.0
    assert champion.MIN_SELECTED_RELIABILITY_TEST == 100
    assert champion.MIN_ROLLING_24H_SLATE_ACCURACY_PCT == 80.0
    assert champion.VERSION == policy.CHAMPION_GATE_VERSION
    assert "80pct-rolling" in champion.VERSION and "90pct" in champion.VERSION
    assert semantics.ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT == 80.0
    assert semantics.MIN_PLAYABLE_TARGET_ACCURACY_PCT == 90.0
    assert optimization._rolling_24h_slate_accuracy({
        "summary": {"rolling24hOfficialCardSlateAccuracyPct": 80.0}
    }) == 80.0
    assert optimization._rolling_24h_slate_accuracy({}) is None

    overlay = SimpleNamespace()
    runtime_safety.apply(overlay)
    runtime_model = {
        "productionApproved": True,
        "promotionTargetAccuracyPct": 90.0,
        "testMetrics": {
            "testCount": 100,
            "selectedCount": 100,
            "selectedAccuracyPct": 90.0,
            "exactOddsCoveragePct": 90.0,
            "selectedCalibrationError": 0.10,
            "rolling24hSlateAccuracyPct": 80.0,
        },
    }
    assert overlay._validated(runtime_model, {}, 90.0) is True
    below_rolling_target = {
        **runtime_model,
        "testMetrics": {**runtime_model["testMetrics"], "rolling24hSlateAccuracyPct": 79.99},
    }
    assert overlay._validated(below_rolling_target, {}, 90.0) is False
    no_rolling_metric = {
        **runtime_model,
        "testMetrics": {**runtime_model["testMetrics"], "rolling24hSlateAccuracyPct": None},
    }
    assert overlay._validated(no_rolling_metric, {}, 90.0) is False

    champion.AUTO_PROMOTE = True
    at_threshold = champion.evaluate(
        _dual_model(), clean_count=500, playable_evidence_count=200, rolling_slate_accuracy_pct=80.0
    )
    assert at_threshold.get("directionPromotionEligible") is True, at_threshold
    assert at_threshold.get("playabilityPromotionEligible") is True, at_threshold
    assert at_threshold.get("promotionDecision") == "PROMOTE"
    assert at_threshold.get("recommendationReliabilityThresholdPct") == 90.0
    assert at_threshold.get("rolling24hAllGamesAuditTargetPct") == 80.0
    assert at_threshold.get("minimumRolling24hSlateAccuracyPct") == 80.0
    assert not any("roi" in str(key).lower() for key in at_threshold.get("playabilityChecks", {}))
    assert not any("roi" in str(item.get("code", "")).lower() for item in at_threshold.get("blockers") or [])

    # Rolling-slate progress below 80% is reporting-only and cannot activate authority.
    for milestone in (50.0, 60.0, 70.0, 79.99):
        progress_only = champion.evaluate(
            _dual_model(), clean_count=500, playable_evidence_count=200,
            rolling_slate_accuracy_pct=milestone,
        )
        assert progress_only.get("directionPromotionEligible") is False, progress_only
        assert progress_only.get("playabilityPromotionEligible") is False, progress_only
        assert progress_only.get("promotionDecision") == "RETAIN_CURRENT_CHAMPION", progress_only

    unavailable = champion.evaluate(_dual_model(), clean_count=500, playable_evidence_count=200)
    assert unavailable.get("directionPromotionEligible") is False
    assert unavailable.get("playabilityPromotionEligible") is False
    assert "ROLLING_24H_SLATE_ACCURACY_UNAVAILABLE" in {
        item.get("code") for item in unavailable.get("blockers") or []
    }

    selected_low = champion.evaluate(
        _dual_model(selected_accuracy=89.99), clean_count=500, playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert selected_low.get("directionPromotionEligible") is True
    assert selected_low.get("playabilityPromotionEligible") is False
    assert "SELECTED_ACCURACY_TOO_LOW" in {
        item.get("code") for item in selected_low.get("playabilityBlockers") or []
    }

    selected_too_small = champion.evaluate(
        _dual_model(selected_count=99), clean_count=500, playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert "INSUFFICIENT_SELECTED_RELIABILITY_TEST_ROWS" in {
        item.get("code") for item in selected_too_small.get("playabilityBlockers") or []
    }

    odds_low = champion.evaluate(
        _dual_model(exact_odds_coverage=89.99), clean_count=500, playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert "SELECTED_EXACT_ODDS_COVERAGE_TOO_LOW" in {
        item.get("code") for item in odds_low.get("playabilityBlockers") or []
    }

    calibration_high = champion.evaluate(
        _dual_model(calibration_error=0.1001), clean_count=500, playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert "RELIABILITY_CALIBRATION_ERROR_TOO_HIGH" in {
        item.get("code") for item in calibration_high.get("playabilityBlockers") or []
    }

    outcome_low = champion.evaluate(
        _dual_model(outcome_accuracy=89.99), clean_count=500, playable_evidence_count=200,
        rolling_slate_accuracy_pct=80.0,
    )
    assert outcome_low.get("directionPromotionEligible") is False
    assert outcome_low.get("playabilityPromotionEligible") is True
    assert "OUTCOME_UNTOUCHED_ACCURACY_BELOW_AUTHORITY_TARGET" in {
        item.get("code") for item in outcome_low.get("directionBlockers") or []
    }

    semantics_source = (HELLO_WORLD / "mlb_real_world_accuracy_semantics_fix.py").read_text(encoding="utf-8")
    assert '"targetAccuracyPct": all_games_audit_target' in semantics_source
    assert '"playableRecommendationAccuracyThresholdPct": playable_threshold' in semantics_source
    assert "auditTargetDoesNotSuppressOfficialPredictions" in semantics_source

    champion_source = (HELLO_WORLD / "mlb_ml_champion_challenger_v1.py").read_text(encoding="utf-8")
    assert 'INQSI_MLB_ML_AUTO_PROMOTE", "false"' in champion_source
    audit_workflow = (ROOT / ".github/workflows/mlb-rolling-24h-audit.yml").read_text(encoding="utf-8")
    assert "INQSI_MLB_ML_AUTO_PROMOTE: 'true'" in audit_workflow

    print(
        "MLB authority targets verified: rolling 24-hour official-card accuracy requires 80%; "
        "untouched outcome and selected-playability reliability remain at 90%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
