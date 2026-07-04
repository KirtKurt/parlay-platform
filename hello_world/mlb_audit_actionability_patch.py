from __future__ import annotations

from typing import Any, Dict, List, Optional


def _is_optimized(row: Dict[str, Any]) -> bool:
    winner_optimizer = row.get("winnerOptimizer") or {}
    winner_stack = row.get("winnerStackV2") or {}
    return bool(row.get("individualWinnerOptimized") or winner_optimizer.get("applied") or winner_stack.get("applied"))


def _is_actionable(row: Dict[str, Any]) -> bool:
    return bool(
        row.get("actionablePick") is True
        or row.get("officialPick") is True
        or row.get("accuracyTargetEligible") is True
    )


def _accuracy(rows: List[Dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get("correct")) / len(rows) * 100.0, 2)


def apply(module):
    if getattr(module, "_INQSI_MLB_AUDIT_ACTIONABILITY_APPLIED", False):
        return module

    def patched_audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = module.predictions_index(finals)
        rows = []
        for final in finals:
            pred = index.get(final.get("gameKeyBase")) or {}
            if not pred:
                rows.append({**final, "status": "MISSING_PREDICTION"})
                continue
            correct = module.normalize_team(pred.get("predictedWinner")) == module.normalize_team(final.get("winner"))
            rows.append({
                **final,
                "status": "GRADED",
                "predictedWinner": pred.get("predictedWinner"),
                "predictedSide": pred.get("predictedSide"),
                "score": pred.get("score"),
                "winProbabilityPct": pred.get("winProbabilityPct"),
                "confidenceTier": pred.get("confidenceTier"),
                "tags": pred.get("tags") or [],
                "selectionBeforeWinnerOptimizer": pred.get("selectionBeforeWinnerOptimizer"),
                "individualWinnerOptimized": pred.get("individualWinnerOptimized"),
                "optimizerFlippedPick": pred.get("optimizerFlippedPick"),
                "winnerOptimizer": pred.get("winnerOptimizer"),
                "winnerStackV2": pred.get("winnerStackV2"),
                "officialPick": pred.get("officialPick"),
                "officialPrediction": pred.get("officialPrediction"),
                "actionablePick": pred.get("actionablePick"),
                "accuracyTargetEligible": pred.get("accuracyTargetEligible"),
                "actionability": pred.get("actionability"),
                "actionabilityReason": pred.get("actionabilityReason"),
                "actionabilityRiskReasons": pred.get("actionabilityRiskReasons") or [],
                "homeSignal": pred.get("homeSignal"),
                "awaySignal": pred.get("awaySignal"),
                "correct": correct,
            })
        return rows

    def patched_summarize(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        historical_rows = historical_rows or []
        graded = [r for r in rows if r.get("status") == "GRADED"]
        correct = [r for r in graded if r.get("correct")]
        optimized = [r for r in graded if _is_optimized(r)]
        optimized_base = optimized if optimized else graded
        optimized_correct = [r for r in optimized_base if r.get("correct")]
        flipped = [r for r in graded if r.get("optimizerFlippedPick")]
        actionable = [r for r in graded if _is_actionable(r)]
        actionable_correct = [r for r in actionable if r.get("correct")]
        all_rows = module._dedupe_rows((rows or []) + (historical_rows or []))
        seven_day_rows = module._rows_since(all_rows, 7)
        thirty_day_rows = module._rows_since(all_rows, 30)
        season_rows = module._rows_since(all_rows, None)
        optimized_accuracy = _accuracy(optimized_base)

        return {
            "windowHours": module.WINDOW_HOURS,
            "targetAccuracyPct": module.TARGET_ACCURACY_PCT,
            "completedFinalGames": len(rows),
            "gradedPredictionCount": len(graded),
            "missingPredictionCount": len(rows) - len(graded),
            "optimizedPickCount": len(optimized_base),
            "optimizedCorrect": len(optimized_correct),
            "optimizedWrong": len(optimized_base) - len(optimized_correct),
            "rolling24hOptimizedAccuracyPct": optimized_accuracy,
            "rolling24hTargetMet": (optimized_accuracy >= module.TARGET_ACCURACY_PCT) if optimized_accuracy is not None else None,
            "winnerOptimizerAppliedCount": len(optimized),
            "winnerOptimizerFlipCount": len(flipped),
            "allScoredPickAccuracyPct": module._accuracy(graded),
            "historicalRowsUsedForLearning": len(module._dedupe_rows(historical_rows)),
            "sevenDayRowsUsedForLearning": len(seven_day_rows),
            "sevenDayAccuracyPct": module._accuracy(seven_day_rows),
            "thirtyDayRowsUsedForLearning": len(thirty_day_rows),
            "thirtyDayAccuracyPct": module._accuracy(thirty_day_rows),
            "seasonRowsUsedForLearning": len(season_rows),
            "seasonAccuracyPct": module._accuracy(season_rows),
            "multiWindowWeights": module.MULTI_WINDOW_WEIGHTS,
            "actionablePickCount": len(actionable),
            "actionableCorrect": len(actionable_correct),
            "actionableWrong": len(actionable) - len(actionable_correct),
            "rolling24hActionableAccuracyPct": _accuracy(actionable),
            "actionabilityPolicy": "Actionable metrics now count only explicit actionablePick/officialPick/accuracyTargetEligible rows; all stored predictions remain graded separately.",
        }

    module.audit_rows = patched_audit_rows
    module.summarize = patched_summarize
    module._INQSI_MLB_AUDIT_ACTIONABILITY_APPLIED = True
    return module
