from __future__ import annotations

from math import sqrt
from typing import Any, Dict, Iterable, Optional


VERSION = "MLB-PRECISION-ADMISSION-v1-70pct-wilson-prospective"
TARGET_PRECISION_PCT = 70.0
MIN_HOLDOUT_GAMES = 50
MIN_DISTINCT_SLATE_DATES = 20
MIN_CHRONOLOGICAL_FOLDS = 3
MIN_GAMES_PER_FOLD = 10
MIN_FOLD_ACCURACY_PCT = 60.0
MIN_RECENT_GAMES = 20
MIN_RECENT_ACCURACY_PCT = 70.0
WILSON_Z_95 = 1.959963984540054


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _b(value: Any) -> bool:
    return value is True


def wilson_lower_bound_pct(correct: int, total: int, z: float = WILSON_Z_95) -> float:
    correct = max(0, int(correct))
    total = max(0, int(total))
    if total <= 0 or correct > total:
        return 0.0
    p = correct / total
    denominator = 1.0 + (z * z / total)
    centre = (p + z * z / (2.0 * total)) / denominator
    half = z * sqrt((p * (1.0 - p) / total) + z * z / (4.0 * total * total)) / denominator
    return max(0.0, (centre - half) * 100.0)


def _accuracy_pct(correct: int, total: int) -> float:
    return (100.0 * correct / total) if total > 0 and 0 <= correct <= total else 0.0


def _fold_metrics(folds: Any) -> list[Dict[str, Any]]:
    output: list[Dict[str, Any]] = []
    for index, fold in enumerate(folds or []):
        if not isinstance(fold, dict):
            continue
        games = _i(fold.get("games") or fold.get("sampleSize"))
        correct = _i(fold.get("correct"))
        output.append(
            {
                "index": index,
                "games": games,
                "correct": correct,
                "accuracyPct": round(_accuracy_pct(correct, games), 2),
                "startDate": fold.get("startDate"),
                "endDate": fold.get("endDate"),
            }
        )
    return output


def evaluate(
    evidence: Any,
    *,
    expected_signal_family: Optional[str] = None,
    expected_similarity_signature: Optional[str] = None,
) -> Dict[str, Any]:
    evidence = evidence if isinstance(evidence, dict) else {}
    holdout = evidence.get("holdout") if isinstance(evidence.get("holdout"), dict) else evidence
    games = _i(holdout.get("games") or holdout.get("sampleSize"))
    correct = _i(holdout.get("correct"))
    distinct_dates = _i(holdout.get("distinctSlateDates"))
    recent_games = _i(holdout.get("recentGames"))
    recent_correct = _i(holdout.get("recentCorrect"))
    folds = _fold_metrics(holdout.get("folds"))
    observed = _accuracy_pct(correct, games)
    lower = wilson_lower_bound_pct(correct, games)
    recent_accuracy = _accuracy_pct(recent_correct, recent_games)
    family = str(evidence.get("signalFamily") or "")
    signature = str(evidence.get("similaritySignature") or "")
    signature_mode = str(evidence.get("signatureMatchMode") or "family").lower()

    reasons: list[str] = []
    if not evidence:
        reasons.append("precision_admission_evidence_missing")
    if expected_signal_family and family != expected_signal_family:
        reasons.append("precision_signal_family_mismatch")
    if expected_similarity_signature and signature_mode == "exact" and signature != expected_similarity_signature:
        reasons.append("precision_similarity_signature_mismatch")
    if not _b(evidence.get("prospective")):
        reasons.append("precision_evidence_not_prospective")
    if not _b(evidence.get("chronologicalHoldout")):
        reasons.append("precision_holdout_not_chronological")
    if not _b(evidence.get("outcomeUntouched")):
        reasons.append("precision_outcomes_not_untouched")
    if not _b(evidence.get("ruleFrozenBeforeEvaluation")):
        reasons.append("precision_rule_not_frozen_before_evaluation")
    if evidence.get("postDiscoveryTuning") is not False:
        reasons.append("precision_post_discovery_tuning_not_excluded")
    if games < MIN_HOLDOUT_GAMES:
        reasons.append("precision_holdout_sample_below_minimum")
    if distinct_dates < MIN_DISTINCT_SLATE_DATES:
        reasons.append("precision_distinct_slate_dates_below_minimum")
    if len(folds) < MIN_CHRONOLOGICAL_FOLDS:
        reasons.append("precision_chronological_folds_below_minimum")
    if any(fold["games"] < MIN_GAMES_PER_FOLD for fold in folds):
        reasons.append("precision_fold_sample_below_minimum")
    if any(float(fold["accuracyPct"]) < MIN_FOLD_ACCURACY_PCT for fold in folds):
        reasons.append("precision_fold_accuracy_unstable")
    if lower < TARGET_PRECISION_PCT:
        reasons.append("precision_wilson_lower_bound_below_70pct")
    if recent_games < MIN_RECENT_GAMES:
        reasons.append("precision_recent_sample_below_minimum")
    elif recent_accuracy < MIN_RECENT_ACCURACY_PCT:
        reasons.append("precision_recent_accuracy_below_70pct")

    reasons = sorted(set(reasons))
    return {
        "applied": True,
        "version": VERSION,
        "admitted": not reasons,
        "signalFamily": family or expected_signal_family,
        "expectedSignalFamily": expected_signal_family,
        "similaritySignature": expected_similarity_signature or signature or None,
        "targetPrecisionPct": TARGET_PRECISION_PCT,
        "minimumHoldoutGames": MIN_HOLDOUT_GAMES,
        "minimumDistinctSlateDates": MIN_DISTINCT_SLATE_DATES,
        "minimumChronologicalFolds": MIN_CHRONOLOGICAL_FOLDS,
        "holdoutGames": games,
        "holdoutCorrect": correct,
        "observedAccuracyPct": round(observed, 2),
        "wilsonLower95Pct": round(lower, 2),
        "distinctSlateDates": distinct_dates,
        "folds": folds,
        "recentGames": recent_games,
        "recentCorrect": recent_correct,
        "recentAccuracyPct": round(recent_accuracy, 2),
        "reasons": reasons,
        "policy": (
            "No official recommendation is admitted from a signal family until a frozen, outcome-untouched, "
            "chronological prospective holdout has at least 50 games across 20 slate dates and three folds, "
            "with a 95% Wilson lower precision bound of at least 70% and no recent collapse. Otherwise abstain."
        ),
    }


def evidence_from_row(row: Any, selected: Any = None) -> Dict[str, Any]:
    for source in (selected, row):
        if not isinstance(source, dict):
            continue
        for key in ("precisionAdmissionEvidence", "signalValidation", "precisionEvidence"):
            evidence = source.get(key)
            if isinstance(evidence, dict):
                return evidence
    return {}
