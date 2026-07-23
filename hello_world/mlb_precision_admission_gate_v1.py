from __future__ import annotations

from datetime import datetime, timezone
from math import sqrt
from typing import Any, Dict, Optional

import mlb_reversal_similarity_v2 as similarity
import mlb_signal_validation_registry_v1 as registry


VERSION = "MLB-PRECISION-ADMISSION-v1-70pct-prospective-wilson"
TARGET_PRECISION = 0.70
MIN_SAMPLE_GAMES = 100
MIN_SLATE_DATES = 20
MIN_CHRONOLOGICAL_FOLDS = 3
MIN_GAMES_PER_FOLD = 20
MIN_FOLD_ACCURACY = 0.65
MIN_RECENT_GAMES = 30
MIN_RECENT_ACCURACY = 0.70
WILSON_Z = 1.959963984540054


def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def wilson_lower_bound(hits: int, sample: int, z: float = WILSON_Z) -> float:
    if sample <= 0:
        return 0.0
    proportion = hits / sample
    denominator = 1.0 + z * z / sample
    center = (proportion + z * z / (2.0 * sample)) / denominator
    margin = z * sqrt((proportion * (1.0 - proportion) + z * z / (4.0 * sample)) / sample) / denominator
    return max(0.0, center - margin)


def evaluate(
    row: Dict[str, Any],
    selected: Dict[str, Any],
    registry_module: Any = registry,
) -> Dict[str, Any]:
    selected = selected if isinstance(selected, dict) else {}
    analysis = similarity.analyze(selected)
    signal_signature = analysis["signature"]
    record = registry_module.get_record(signal_signature) if hasattr(registry_module, "get_record") else None
    reasons = []

    if not isinstance(record, dict):
        reasons.append("no_code_reviewed_prospective_validation_record")
        return {
            "applied": True,
            "version": VERSION,
            "recommendationEligible": False,
            "signalSignature": signal_signature,
            "targetPrecisionPct": TARGET_PRECISION * 100.0,
            "futureAccuracyGuaranteed": False,
            "validationRecord": None,
            "reasons": reasons,
            "policy": _policy(),
        }

    trusted = bool(
        hasattr(registry_module, "record_is_trusted")
        and registry_module.record_is_trusted(record)
    )
    if not trusted:
        reasons.append("validation_record_not_trusted_by_deployed_registry")

    if str(record.get("signalSignature") or "") != signal_signature:
        reasons.append("signal_signature_mismatch")
    if str(record.get("similarityVersion") or "") != similarity.VERSION:
        reasons.append("similarity_definition_version_mismatch")
    if record.get("prospective") is not True:
        reasons.append("validation_not_prospective")
    if record.get("outcomeUntouched") is not True:
        reasons.append("validation_not_outcome_untouched")
    if record.get("chronological") is not True:
        reasons.append("validation_not_chronological")
    if record.get("selectionRuleFrozenBeforeEvaluation") is not True:
        reasons.append("selection_rule_not_frozen_before_evaluation")
    if record.get("noOutcomeBasedTuning") is not True:
        reasons.append("outcome_based_tuning_not_excluded")
    if record.get("independentReproduction") is not True:
        reasons.append("independent_reproduction_missing")
    if record.get("productionApproved") is not True:
        reasons.append("record_not_production_approved")

    frozen_at = _parse_dt(record.get("frozenAtUtc"))
    evaluation_started = _parse_dt(record.get("evaluationStartedAtUtc"))
    if not frozen_at or not evaluation_started or frozen_at >= evaluation_started:
        reasons.append("freeze_time_not_before_evaluation")

    artifact_sha = str(record.get("auditArtifactSha256") or "")
    if len(artifact_sha) != 64 or any(character not in "0123456789abcdefABCDEF" for character in artifact_sha):
        reasons.append("audit_artifact_sha256_missing_or_invalid")

    sample = _i(record.get("sampleGames"))
    hits = _i(record.get("hits"))
    misses = _i(record.get("misses"))
    dates = _i(record.get("slateDates"))
    if sample < MIN_SAMPLE_GAMES:
        reasons.append("prospective_sample_below_100_games")
    if dates < MIN_SLATE_DATES:
        reasons.append("prospective_slate_dates_below_20")
    if hits < 0 or misses < 0 or hits + misses != sample:
        reasons.append("validation_hit_miss_count_mismatch")
    observed_accuracy = hits / sample if sample > 0 and hits + misses == sample else 0.0
    lower_bound = wilson_lower_bound(hits, sample) if sample > 0 and hits + misses == sample else 0.0
    if observed_accuracy < TARGET_PRECISION:
        reasons.append("observed_precision_below_70pct")
    if lower_bound < TARGET_PRECISION:
        reasons.append("wilson_lower_bound_below_70pct")

    folds = record.get("chronologicalFolds") or []
    if not isinstance(folds, list) or len(folds) < MIN_CHRONOLOGICAL_FOLDS:
        reasons.append("fewer_than_three_chronological_folds")
        folds = []
    fold_sample_total = 0
    for index, fold in enumerate(folds):
        if not isinstance(fold, dict):
            reasons.append(f"fold_{index + 1}_invalid")
            continue
        fold_games = _i(fold.get("games"))
        fold_hits = _i(fold.get("hits"))
        fold_misses = _i(fold.get("misses"))
        fold_sample_total += fold_games
        if fold_games < MIN_GAMES_PER_FOLD:
            reasons.append(f"fold_{index + 1}_below_20_games")
        if fold_hits + fold_misses != fold_games:
            reasons.append(f"fold_{index + 1}_count_mismatch")
            continue
        fold_accuracy = fold_hits / fold_games if fold_games else 0.0
        if fold_accuracy < MIN_FOLD_ACCURACY:
            reasons.append(f"fold_{index + 1}_accuracy_below_65pct")
    if folds and fold_sample_total != sample:
        reasons.append("fold_sample_total_mismatch")

    recent_games = _i(record.get("recentGames"))
    recent_hits = _i(record.get("recentHits"))
    recent_misses = _i(record.get("recentMisses"))
    if recent_games < MIN_RECENT_GAMES:
        reasons.append("recent_window_below_30_games")
    if recent_hits + recent_misses != recent_games:
        reasons.append("recent_window_count_mismatch")
    else:
        recent_accuracy = recent_hits / recent_games if recent_games else 0.0
        if recent_accuracy < MIN_RECENT_ACCURACY:
            reasons.append("recent_precision_below_70pct")

    reasons = sorted(set(reasons))
    return {
        "applied": True,
        "version": VERSION,
        "recommendationEligible": not reasons,
        "signalSignature": signal_signature,
        "targetPrecisionPct": TARGET_PRECISION * 100.0,
        "sampleGames": sample,
        "slateDates": dates,
        "observedPrecisionPct": round(observed_accuracy * 100.0, 3),
        "wilsonLowerBoundPct": round(lower_bound * 100.0, 3),
        "futureAccuracyGuaranteed": False,
        "evidenceAdmissionGuaranteed": True,
        "validationRecordFingerprint": record.get("recordFingerprint"),
        "reasons": reasons,
        "policy": _policy(),
    }


def _policy() -> str:
    return (
        "A signal may be labeled recommendation-eligible only when its exact frozen similarity signature is packaged "
        "in the deployed code-reviewed registry and has at least 100 prospective outcome-untouched games across 20 "
        "slate dates, three chronological folds of at least 20 games, every fold at 65% or better, a 30-game recent "
        "window at 70% or better, and a 95% Wilson lower confidence bound of at least 70%. This controls the label; "
        "it cannot guarantee future game outcomes."
    )
