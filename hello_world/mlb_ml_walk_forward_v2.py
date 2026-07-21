from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "MLB-ML-WALK-FORWARD-v2-fixed-slate-prospective-metrics"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def clip_probability(value: Any) -> float:
    return max(1e-6, min(1.0 - 1e-6, _f(value, 0.5)))


def accuracy(rows: Sequence[Dict[str, Any]], probability_key: str, label_key: str, threshold: float = 0.5) -> Optional[float]:
    if not rows:
        return None
    correct = 0
    for row in rows:
        prediction = 1 if clip_probability(row.get(probability_key)) >= threshold else 0
        correct += int(prediction == int(row.get(label_key) or 0))
    return round(correct / len(rows) * 100.0, 2)


def brier(rows: Sequence[Dict[str, Any]], probability_key: str, label_key: str) -> Optional[float]:
    if not rows:
        return None
    return round(sum((clip_probability(row.get(probability_key)) - int(row.get(label_key) or 0)) ** 2 for row in rows) / len(rows), 6)


def log_loss(rows: Sequence[Dict[str, Any]], probability_key: str, label_key: str) -> Optional[float]:
    if not rows:
        return None
    total = 0.0
    for row in rows:
        probability = clip_probability(row.get(probability_key))
        label = int(row.get(label_key) or 0)
        total += -(label * math.log(probability) + (1 - label) * math.log(1 - probability))
    return round(total / len(rows), 6)


def calibration_error(rows: Sequence[Dict[str, Any]], probability_key: str, label_key: str, bucket_width: float = 0.1) -> Optional[float]:
    if not rows:
        return None
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        probability = clip_probability(row.get(probability_key))
        bucket = min(int(probability / bucket_width), int(1.0 / bucket_width) - 1)
        buckets.setdefault(bucket, []).append(row)
    weighted = 0.0
    for bucket_rows in buckets.values():
        average_probability = sum(clip_probability(row.get(probability_key)) for row in bucket_rows) / len(bucket_rows)
        actual = sum(int(row.get(label_key) or 0) for row in bucket_rows) / len(bucket_rows)
        weighted += abs(average_probability - actual) * len(bucket_rows)
    return round(weighted / len(rows), 6)


def paired_accuracy_regression(
    rows: Sequence[Dict[str, Any]],
    probability_key: str,
    baseline_probability_key: str,
    label_key: str,
) -> Dict[str, Any]:
    """One-sided exact McNemar/binomial evidence that the model is worse.

    The promotion policy separately requires a positive point lift. This test
    prevents a challenger from passing if discordant predictions show
    statistically significant regression against the same-game market baseline.
    """
    model_only_correct = 0
    baseline_only_correct = 0
    for row in rows:
        label = int(row.get(label_key) or 0)
        model_correct = int(clip_probability(row.get(probability_key)) >= 0.5) == label
        baseline_correct = int(clip_probability(row.get(baseline_probability_key)) >= 0.5) == label
        if model_correct and not baseline_correct:
            model_only_correct += 1
        elif baseline_correct and not model_correct:
            baseline_only_correct += 1
    discordant = model_only_correct + baseline_only_correct
    if discordant == 0:
        p_value = 1.0
    else:
        # Under equal paired accuracy, baseline-only wins are Binomial(n, .5).
        p_value = sum(math.comb(discordant, k) for k in range(baseline_only_correct, discordant + 1)) / (2 ** discordant)
    regression = bool(baseline_only_correct > model_only_correct and p_value < 0.05)
    return {
        "ok": True,
        "method": "one_sided_exact_mcnemar_binomial",
        "modelOnlyCorrect": model_only_correct,
        "baselineOnlyCorrect": baseline_only_correct,
        "discordantCount": discordant,
        "regressionPValue": round(p_value, 8),
        "statisticallySignificantRegression": regression,
        "alpha": 0.05,
    }


def split_chronological(
    records: Iterable[Dict[str, Any]],
    time_key: str = "commenceTime",
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    min_train: int = 80,
    min_validation: int = 30,
    min_test: int = 30,
) -> Dict[str, Any]:
    raise RuntimeError(
        "dynamic V2 partitioning is disabled; partition whole slate dates once "
        "with mlb_ml_experiment_v2.advance_manifest"
    )


def select_reliability_threshold(
    validation_rows: Sequence[Dict[str, Any]],
    probability_key: str = "reliabilityProbability",
    label_key: str = "pickCorrect",
    minimum_selected: int = 30,
    minimum_coverage: float = 0.10,
) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    total = len(validation_rows)
    for integer in range(50, 91):
        threshold = integer / 100.0
        selected = [row for row in validation_rows if clip_probability(row.get(probability_key)) >= threshold]
        if len(selected) < minimum_selected:
            continue
        coverage = len(selected) / total if total else 0.0
        if coverage < minimum_coverage:
            continue
        candidates.append({
            "threshold": threshold,
            "selectedCount": len(selected),
            "coveragePct": round(coverage * 100.0, 2),
            "accuracyPct": accuracy(selected, probability_key, label_key, threshold=threshold),
            "brierScore": brier(selected, probability_key, label_key),
            "logLoss": log_loss(selected, probability_key, label_key),
        })
    if not candidates:
        return {
            "ok": False,
            "reason": "no_validation_threshold_meets_minimum_sample_and_coverage",
            "minimumSelected": minimum_selected,
            "minimumCoveragePct": minimum_coverage * 100.0,
            "validationRows": total,
        }
    selected = sorted(
        candidates,
        key=lambda row: (
            -float(row.get("brierScore") if row.get("brierScore") is not None else 9.0),
            float(row.get("accuracyPct") or 0.0),
            float(row.get("coveragePct") or 0.0),
        ),
        reverse=True,
    )[0]
    return {"ok": True, **selected, "candidateCount": len(candidates), "selectionSource": "validation_only"}


def evaluate(
    rows: Sequence[Dict[str, Any]],
    probability_key: str,
    label_key: str,
    threshold: float = 0.5,
    baseline_probability_key: Optional[str] = None,
) -> Dict[str, Any]:
    output = {
        "count": len(rows),
        "accuracyPct": accuracy(rows, probability_key, label_key, threshold=threshold),
        "brierScore": brier(rows, probability_key, label_key),
        "logLoss": log_loss(rows, probability_key, label_key),
        "calibrationError": calibration_error(rows, probability_key, label_key),
        "threshold": threshold,
    }
    if baseline_probability_key:
        baseline = {
            "accuracyPct": accuracy(rows, baseline_probability_key, label_key, threshold=0.5),
            "brierScore": brier(rows, baseline_probability_key, label_key),
            "logLoss": log_loss(rows, baseline_probability_key, label_key),
            "calibrationError": calibration_error(rows, baseline_probability_key, label_key),
        }
        output["baseline"] = baseline
        if output["accuracyPct"] is not None and baseline["accuracyPct"] is not None:
            output["accuracyLiftPctPoints"] = round(output["accuracyPct"] - baseline["accuracyPct"], 2)
        if output["brierScore"] is not None and baseline["brierScore"] not in {None, 0}:
            output["brierSkillPct"] = round((baseline["brierScore"] - output["brierScore"]) / baseline["brierScore"] * 100.0, 2)
        output["pairedAccuracyRegression"] = paired_accuracy_regression(
            rows, probability_key, baseline_probability_key, label_key
        )
    return output


def run(
    records: Sequence[Dict[str, Any]],
    fit: Callable[[Sequence[Dict[str, Any]]], Dict[str, Any]],
    score: Callable[[Dict[str, Any], Dict[str, Any]], float],
    probability_key: str,
    label_key: str,
    baseline_probability_key: Optional[str] = None,
) -> Dict[str, Any]:
    raise RuntimeError(
        "dynamic V2 walk-forward execution is disabled; use the immutable "
        "experiment manifest and mlb_ml_dual_model_v2.train_fixed_partitions"
    )
