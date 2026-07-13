from __future__ import annotations

import os
from typing import Any, Dict

VERSION = "MLB-ACCURACY-TARGET-POLICY-v4-80pct-production-60pct-game-lock"
ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = 80.0
RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = 80.0
MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT = 80.0
MIN_CLEAN_OFFICIAL = 500
MIN_UNTOUCHED_TEST = 100
MIN_SELECTED_UNTOUCHED_TEST = 100
MIN_EXACT_ODDS_COVERAGE_PCT = 80.0
MAX_RELIABILITY_CALIBRATION_ERROR = 0.10
MIN_ROLLING_24H_SLATE_ACCURACY_PCT = 80.0
MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT = 60.0
RELIABILITY_PROGRESS_MILESTONES_PCT = (50.0, 60.0, 70.0, 80.0)
RUNTIME_SAFETY_VERSION = "MLB-ML-RUNTIME-SAFETY-v7-80pct-production-60pct-lock"
CHAMPION_GATE_VERSION = "MLB-ML-CHAMPION-CHALLENGER-v1.7-80pct-production-60pct-lock"


def install() -> Dict[str, Any]:
    """Install the 80% production policy and 60% individual-game lock floor."""
    production = str(RECOMMENDATION_RELIABILITY_THRESHOLD_PCT)
    audit = str(ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT)
    rolling_authority = str(MIN_ROLLING_24H_SLATE_ACCURACY_PCT)
    exact_odds = str(MIN_EXACT_ODDS_COVERAGE_PCT)
    game_lock = str(MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT)

    # Assign rather than setdefault so stale Lambda environment values cannot
    # restore the former 90% production thresholds.
    os.environ["INQSI_MLB_ML_TARGET_ACCURACY"] = production
    os.environ["INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY"] = production
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_ACCURACY"] = production
    os.environ["INQSI_MLB_ML_MIN_OUTCOME_UNTOUCHED_ACCURACY"] = production
    os.environ["INQSI_MLB_ML_MIN_CLEAN_OFFICIAL_FOR_PROMOTION"] = str(MIN_CLEAN_OFFICIAL)
    os.environ["INQSI_MLB_ML_MIN_UNTOUCHED_TEST_FOR_PROMOTION"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_RELIABILITY_TEST"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_TEST_ROWS"] = str(MIN_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_PRODUCTION_SELECTED_TEST_ROWS"] = str(MIN_SELECTED_UNTOUCHED_TEST)
    os.environ["INQSI_MLB_ML_MIN_SELECTED_PRICE_COVERAGE"] = exact_odds
    os.environ["INQSI_MLB_ML_MAX_RELIABILITY_CALIBRATION_ERROR"] = str(MAX_RELIABILITY_CALIBRATION_ERROR)
    os.environ["INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY"] = audit
    os.environ["INQSI_MLB_ROLLING_24H_SLATE_AUTHORITY_TARGET_ACCURACY"] = rolling_authority
    os.environ["INQSI_MLB_INDIVIDUAL_GAME_LOCK_MIN_PROBABILITY_PCT"] = game_lock

    patched = []
    warnings = []
    errors = []

    try:
        import mlb_accuracy_target_patch as winner_target

        winner_target.ROLLING_TARGET_ACCURACY_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        patched.append("winner_rolling_target_80pct")
    except Exception as exc:
        warnings.append(f"winner_target:{exc}")

    try:
        import mlb_rolling_24h_audit as rolling_audit

        rolling_audit.TARGET_ACCURACY_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        patched.append("rolling_audit_target_80pct")
    except Exception as exc:
        warnings.append(f"rolling_audit:{exc}")

    try:
        import mlb_real_world_accuracy_semantics_fix as semantics

        semantics.ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
        semantics.MIN_PLAYABLE_TARGET_ACCURACY_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        patched.append("real_world_accuracy_80pct_production")
    except Exception as exc:
        warnings.append(f"real_world_semantics:{exc}")

    try:
        import mlb_ml_runtime_safety_patch as runtime_safety

        runtime_safety.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        runtime_safety.MIN_PRODUCTION_TEST_ROWS = MIN_UNTOUCHED_TEST
        runtime_safety.MIN_PRODUCTION_SELECTED_TEST_ROWS = MIN_SELECTED_UNTOUCHED_TEST
        runtime_safety.MIN_EXACT_ODDS_COVERAGE_PCT = MIN_EXACT_ODDS_COVERAGE_PCT
        runtime_safety.MAX_RELIABILITY_CALIBRATION_ERROR = MAX_RELIABILITY_CALIBRATION_ERROR
        runtime_safety.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        runtime_safety.VERSION = RUNTIME_SAFETY_VERSION
        try:
            import mlb_ml_runtime_overlay as overlay

            overlay.RUNTIME_SAFETY_VERSION = RUNTIME_SAFETY_VERSION
            overlay.MIN_ACCURACY_TARGET_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
            overlay.MIN_EXACT_ODDS_COVERAGE_PCT = MIN_EXACT_ODDS_COVERAGE_PCT
            overlay.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        except Exception:
            pass
        patched.append("runtime_safety_80pct_production")
    except Exception as exc:
        errors.append(f"runtime_safety:{exc}")

    try:
        import mlb_ml_champion_challenger_v1 as champion

        champion.MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT = MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT
        champion.MIN_SELECTED_RELIABILITY_ACCURACY = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.MIN_CLEAN_OFFICIAL = MIN_CLEAN_OFFICIAL
        champion.MIN_UNTOUCHED_TEST = MIN_UNTOUCHED_TEST
        champion.MIN_SELECTED_RELIABILITY_TEST = MIN_SELECTED_UNTOUCHED_TEST
        champion.MIN_SELECTED_PRICE_COVERAGE = MIN_EXACT_ODDS_COVERAGE_PCT
        champion.MAX_RELIABILITY_CALIBRATION_ERROR = MAX_RELIABILITY_CALIBRATION_ERROR
        champion.MIN_ROLLING_24H_SLATE_ACCURACY_PCT = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
        champion.RELIABILITY_PROGRESS_MILESTONES_PCT = RELIABILITY_PROGRESS_MILESTONES_PCT
        champion.RECOMMENDATION_RELIABILITY_THRESHOLD_PCT = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
        champion.VERSION = CHAMPION_GATE_VERSION

        if not getattr(champion, "_INQSI_MLB_80PCT_PRODUCTION_60PCT_LOCK_POLICY_APPLIED", False):
            original_evaluate = champion.evaluate

            def evaluate_with_policy(
                dual_model,
                clean_count,
                playable_evidence_count,
                rolling_slate_accuracy_pct=None,
            ):
                result = original_evaluate(
                    dual_model,
                    clean_count,
                    playable_evidence_count,
                    rolling_slate_accuracy_pct=rolling_slate_accuracy_pct,
                )
                if isinstance(result, dict):
                    result["version"] = CHAMPION_GATE_VERSION
                    result["rolling24hAllGamesAuditTargetPct"] = ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT
                    result["recommendationReliabilityThresholdPct"] = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
                    result["minimumOutcomeUntouchedAccuracyPct"] = MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT
                    result["selectedUntouchedTestPlayabilityAccuracyTargetPct"] = RECOMMENDATION_RELIABILITY_THRESHOLD_PCT
                    result["minimumSelectedExactOddsCoveragePct"] = MIN_EXACT_ODDS_COVERAGE_PCT
                    result["minimumRolling24hSlateAccuracyPct"] = MIN_ROLLING_24H_SLATE_ACCURACY_PCT
                    result["minimumIndividualGameLockProbabilityPct"] = MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT
                    result["rolling24hSlateAccuracyProgressMilestonesPct"] = list(RELIABILITY_PROGRESS_MILESTONES_PCT)
                    result["rolling24hSlateAccuracyProgressMilestonesReportingOnly"] = True
                    result["policy"] = (
                        "Direction and playability promote independently only after the current rolling MLB slate, "
                        "untouched outcome test, and selected reliability test each meet 80%. Exact locked-odds "
                        "coverage must also reach 80%. Official individual-game locks require at least 60% selected-team "
                        "lock-time probability. Existing clean-row, untouched-test, calibration, and market-lift gates remain."
                    )
                return result

            champion.evaluate = evaluate_with_policy
            champion._INQSI_MLB_80PCT_PRODUCTION_60PCT_LOCK_POLICY_APPLIED = True

        patched.append("champion_80pct_production")
    except Exception as exc:
        errors.append(f"champion:{exc}")

    lock_policy_status: Dict[str, Any]
    try:
        import mlb_individual_game_lock_probability_policy_v1 as lock_policy

        lock_policy_status = lock_policy.install()
        if lock_policy_status.get("ok") is not True:
            errors.append(f"individual_game_lock_policy:{lock_policy_status.get('warnings')}")
        else:
            patched.extend(lock_policy_status.get("patched") or [])
    except Exception as exc:
        lock_policy_status = {"ok": False, "error": str(exc)}
        errors.append(f"individual_game_lock_policy:{exc}")

    return {
        "ok": not errors,
        "version": VERSION,
        "rolling24hAllGamesAuditTargetPct": ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT,
        "recommendationReliabilityThresholdPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "minimumOutcomeUntouchedAccuracyPct": MIN_OUTCOME_UNTOUCHED_ACCURACY_PCT,
        "selectedUntouchedTestPlayabilityAccuracyTargetPct": RECOMMENDATION_RELIABILITY_THRESHOLD_PCT,
        "minimumCleanOfficial": MIN_CLEAN_OFFICIAL,
        "minimumUntouchedTest": MIN_UNTOUCHED_TEST,
        "minimumSelectedUntouchedTest": MIN_SELECTED_UNTOUCHED_TEST,
        "minimumExactOddsCoveragePct": MIN_EXACT_ODDS_COVERAGE_PCT,
        "maximumReliabilityCalibrationError": MAX_RELIABILITY_CALIBRATION_ERROR,
        "minimumRolling24hSlateAccuracyPct": MIN_ROLLING_24H_SLATE_ACCURACY_PCT,
        "minimumIndividualGameLockProbabilityPct": MIN_INDIVIDUAL_GAME_LOCK_PROBABILITY_PCT,
        "rolling24hSlateAccuracyProgressMilestonesPct": list(RELIABILITY_PROGRESS_MILESTONES_PCT),
        "rolling24hSlateAccuracyProgressMilestonesReportingOnly": True,
        "automaticPromotionAfterApplicableGates": True,
        "individualGameLockPolicy": lock_policy_status,
        "patched": sorted(set(patched)),
        "warnings": warnings,
        "errors": errors,
        "policy": (
            "MLB rolling performance, outcome authority, playable reliability, and exact locked-odds coverage each use "
            "an 80% production threshold. An individual game becomes an official locked pick only at a selected-team "
            "lock-time probability of 60% or higher. Sub-60% predictions remain visible audit diagnostics."
        ),
    }
