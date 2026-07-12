from __future__ import annotations

import os
from typing import Any, Dict, Optional

VERSION = "MLB-REAL-WORLD-ACCURACY-v1.5-90pct-all-games-audit-60pct-recommendation"
ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT = 90.0
MIN_PLAYABLE_TARGET_ACCURACY_PCT = 60.0


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _target_floor(value: Any, minimum: float) -> float:
    parsed = _f(value, minimum)
    return max(float(minimum), float(parsed or minimum))


def _install_critical_ml_fixes() -> Dict[str, Any]:
    try:
        import mlb_ml_critical_blockers_patch
        return mlb_ml_critical_blockers_patch.install()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply(accuracy_module: Any):
    critical_fix_status = _install_critical_ml_fixes()
    if getattr(accuracy_module, "_INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED_V15", False):
        return accuracy_module

    def strict_playable(row: Dict[str, Any]) -> bool:
        tags = accuracy_module._tags(row)
        recommendation = str(row.get("recommendationStatus") or "").upper()
        actionability = str(row.get("actionability") or "").upper()
        blocked = bool(
            "NOT_PLAYABLE" in tags or "ML_REJECTED" in tags or "NOT_PLAYABLE" in recommendation
            or "LOW_CONFIDENCE" in recommendation or "NOT_PLAYABLE" in actionability
            or "LOW_CONFIDENCE" in actionability or actionability in {"PASS_NO_PICK", "NO_PICK", "NO_ACTIONABLE_PICK"}
        )
        if blocked:
            return False
        if recommendation == "PLAYABLE_PREDICTION" or "PLAYABLE_PREDICTION" in tags or "ML_CONFIRMED" in tags:
            return True
        modern_semantics = bool(
            row.get("predictionSemanticsVersion") or row.get("playabilityStatus") in {"PLAYABLE", "NOT_PLAYABLE"}
            or row.get("officialPredictionStatus") == "OFFICIAL_LOCKED_PREDICTION"
        )
        if modern_semantics:
            return bool(
                row.get("playable") is True or row.get("playablePick") is True or row.get("actionablePick") is True
                or row.get("accuracyTargetEligible") is True or "ACTIONABLE_PICK" in tags
            )
        return bool("ACTIONABLE_PICK" in tags and "NOT_PLAYABLE" not in tags)

    def true_team_probability(row: Dict[str, Any]) -> Optional[float]:
        direct = _f(row.get("teamWinProbabilityPct"))
        if direct is not None:
            direct = direct / 100.0 if direct > 1.0 else direct
            if 0.0 < direct < 1.0:
                return direct
        meaning = str(row.get("winProbabilityMeaning") or "").lower()
        semantics_fixed = bool(
            row.get("probabilitySemanticsFixed") is True or row.get("predictionSemanticsVersion")
            or meaning in {"estimated_probability_selected_team_wins_game", "approved_outcome_model_probability_selected_team_wins"}
        )
        if semantics_fixed:
            value = _f(row.get("winProbabilityPct"))
            if value is not None:
                value = value / 100.0 if value > 1.0 else value
                if 0.0 < value < 1.0:
                    return value
        return accuracy_module._market_probability(accuracy_module._selected_signal(row))

    def selected_side_odds(row: Dict[str, Any]) -> Optional[float]:
        signal = accuracy_module._selected_signal(row)
        for key in ("americanOdds", "moneyline", "selectedMoneyline", "price"):
            odds = accuracy_module._american_odds(signal.get(key))
            if odds is not None:
                return odds
        for key in ("selectedAmericanOdds", "americanOdds", "moneyline", "selectedMoneyline", "lockedAmericanOdds"):
            odds = accuracy_module._american_odds(row.get(key))
            if odds is not None:
                return odds
        return None

    original_normalize = accuracy_module._normalize_audit_row
    def normalize_with_ledger_status(row: Dict[str, Any]) -> Dict[str, Any]:
        source = dict(row or {})
        if source.get("status") is None and source.get("correct") in {True, False} and source.get("predictedWinner") and source.get("winner"):
            source["status"] = "GRADED"
        return original_normalize(source)

    original_ledger_row = accuracy_module._ledger_row
    def ledger_row_with_status(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(original_ledger_row(row))
        out["status"] = "GRADED"
        for key in ("mlFeatureFreeze", "frozenOutcomeFeatures", "frozenReliabilityFeatures", "featureVectorFrozenAtLock"):
            if row.get(key) is not None:
                out[key] = row.get(key)
        return out

    original_enhance_report = accuracy_module.enhance_report
    def enhanced_report(module: Any, report: Dict[str, Any], historical_rows=None, ledger_rows=None) -> Dict[str, Any]:
        out = original_enhance_report(module, report, historical_rows=historical_rows, ledger_rows=ledger_rows)
        windows = ((out.get("realWorldAccuracy") or {}).get("windows") or {})
        current = windows.get("current24h") or {}
        seven = windows.get("sevenDay") or {}
        thirty = windows.get("thirtyDay") or {}
        season = windows.get("season") or {}
        current_official = current.get("officialPredictions") or {}
        current_playable = current.get("playableRecommendations") or {}
        season_official = season.get("officialPredictions") or {}
        season_playable = season.get("playableRecommendations") or {}
        summary = dict(out.get("summary") or {})

        official_accuracy = current_official.get("accuracyPct")
        playable_accuracy = current_playable.get("accuracyPct")
        all_games_audit_target = _target_floor(
            os.environ.get("INQSI_MLB_ROLLING_24H_ALL_GAMES_TARGET_ACCURACY", "90"),
            ROLLING_24H_ALL_GAMES_AUDIT_TARGET_PCT,
        )
        playable_threshold = _target_floor(
            os.environ.get("INQSI_MLB_ML_PLAYABLE_TARGET_ACCURACY", "60"),
            MIN_PLAYABLE_TARGET_ACCURACY_PCT,
        )

        summary.update({
            "targetAccuracyPct": all_games_audit_target,
            "rolling24hAllGamesAccuracyPct": official_accuracy,
            "rolling24hTargetMet": (official_accuracy >= all_games_audit_target) if official_accuracy is not None else None,
            "optimizedPickCount": current_playable.get("count"),
            "optimizedCorrect": current_playable.get("correct"),
            "optimizedWrong": current_playable.get("wrong"),
            "rolling24hOptimizedAccuracyPct": playable_accuracy,
            "playableRecommendationAccuracyThresholdPct": playable_threshold,
            "rolling24hPlayableThresholdMet": (playable_accuracy >= playable_threshold) if playable_accuracy is not None else None,
            "allScoredPickAccuracyPct": official_accuracy,
            "sevenDayRowsUsedForLearning": (seven.get("officialPredictions") or {}).get("count"),
            "sevenDayAccuracyPct": (seven.get("officialPredictions") or {}).get("accuracyPct"),
            "thirtyDayRowsUsedForLearning": (thirty.get("officialPredictions") or {}).get("count"),
            "thirtyDayAccuracyPct": (thirty.get("officialPredictions") or {}).get("accuracyPct"),
            "seasonRowsUsedForLearning": season_official.get("count"),
            "seasonAccuracyPct": season_official.get("accuracyPct"),
            "seasonOfficialPredictionCount": season_official.get("count"),
            "seasonPlayablePredictionCount": season_playable.get("count"),
            "accuracyTargetRowPolicy": "90pct_target_applies_only_to_all_official_games_in_the_rolling_24h_audit; recommendation_reliability_threshold_is_60pct",
            "actionabilityPolicy": "Playable metrics require explicit modern playability or ACTIONABLE_PICK/ML_CONFIRMED. officialPick is never a playability signal.",
            "officialCardPolicy": "Every immutable locked winner is graded as an official prediction, regardless of playability.",
            "optimizationTargetPolicy": "Reliability/recommendation validation uses a 60% selected-sample threshold plus positive ROI, adequate price coverage, and acceptable calibration. Outcome direction must independently beat the de-vigged market baseline. The 90% figure is reporting-only for the rolling 24-hour all-games audit.",
            "accuracyClassificationVersion": VERSION,
        })
        out["summary"] = summary
        rwa = dict(out.get("realWorldAccuracy") or {})
        rwa["version"] = VERSION
        rwa["legacySummaryFieldsNormalized"] = True
        rwa["legacyProbabilityPolicy"] = "Use selected-side de-vigged market probability for legacy reporting; legacy rows are excluded from ML v3 training."
        rwa["legacyPlayabilityPolicy"] = "Do not accept officialPick/actionablePick alone as proof of a playable recommendation on legacy rows."
        rwa["selectedOddsPolicy"] = "Use the final predicted side's stored signal price; row-level pre-flip prices are fallback only."
        rwa["ledgerReadbackPolicy"] = "Immutable ledger rows without an older status field are treated as GRADED when final winner and correctness are stored."
        rwa["mlCriticalFixStatus"] = critical_fix_status
        rwa["mlTrainingPolicy"] = "Only immutable lock-time feature vectors with current semantics and complete slate coverage may train the dual-model ML v3 system."
        rwa["rolling24hAllGamesAuditTargetPct"] = all_games_audit_target
        rwa["playableAccuracyTargetPct"] = playable_threshold
        rwa["auditTargetDoesNotSuppressRecommendations"] = True
        out["realWorldAccuracy"] = rwa
        return out

    accuracy_module._is_playable = strict_playable
    accuracy_module._team_probability = true_team_probability
    accuracy_module._selected_odds = selected_side_odds
    accuracy_module._normalize_audit_row = normalize_with_ledger_status
    accuracy_module._ledger_row = ledger_row_with_status
    accuracy_module.enhance_report = enhanced_report
    accuracy_module.VERSION = VERSION

    try:
        import mlb_audit_actionability_patch as actionability_patch
        actionability_patch._is_actionable = strict_playable
    except Exception:
        pass

    accuracy_module._INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED = True
    accuracy_module._INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED_V12 = True
    accuracy_module._INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED_V13 = True
    accuracy_module._INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED_V14 = True
    accuracy_module._INQSI_MLB_REAL_WORLD_ACCURACY_SEMANTICS_FIXED_V15 = True
    return accuracy_module
