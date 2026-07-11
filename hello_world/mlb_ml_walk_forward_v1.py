from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "MLB-ML-WALK-FORWARD-v1-train-validation-untouched-test"


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


def split_chronological(
    records: Iterable[Dict[str, Any]],
    time_key: str = "commenceTime",
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
    min_train: int = 80,
    min_validation: int = 30,
    min_test: int = 30,
) -> Dict[str, Any]:
    rows = sorted(list(records or []), key=lambda row: str(row.get(time_key) or ""))
    count = len(rows)
    train_end = max(min_train, int(count * train_fraction))
    validation_count = max(min_validation, int(count * validation_fraction))
    validation_end = train_end + validation_count
    if count < min_train + min_validation + min_test or validation_end > count - min_test:
        return {
            "ok": False,
            "version": VERSION,
            "reason": "insufficient_clean_rows_for_three_way_chronological_split",
            "rowCount": count,
            "required": min_train + min_validation + min_test,
            "minimums": {"train": min_train, "validation": min_validation, "test": min_test},
            "train": [],
            "validation": [],
            "test": [],
        }
    return {
        "ok": True,
        "version": VERSION,
        "rowCount": count,
        "train": rows[:train_end],
        "validation": rows[train_end:validation_end],
        "test": rows[validation_end:],
        "counts": {"train": train_end, "validation": validation_count, "test": count - validation_end},
        "boundaries": {
            "trainEnd": rows[train_end - 1].get(time_key),
            "validationStart": rows[train_end].get(time_key),
            "validationEnd": rows[validation_end - 1].get(time_key),
            "testStart": rows[validation_end].get(time_key),
        },
        "policy": "Thresholds and model choices use validation only. The test period remains untouched until the challenger is finalized.",
    }


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
    return output


def run(
    records: Sequence[Dict[str, Any]],
    fit: Callable[[Sequence[Dict[str, Any]]], Dict[str, Any]],
    score: Callable[[Dict[str, Any], Dict[str, Any]], float],
    probability_key: str,
    label_key: str,
    baseline_probability_key: Optional[str] = None,
) -> Dict[str, Any]:
    split = split_chronological(records)
    if not split.get("ok"):
        return {"ok": False, "version": VERSION, "split": split}
    model = fit(split["train"])
    validation = [dict(row, **{probability_key: score(row, model)}) for row in split["validation"]]
    test = [dict(row, **{probability_key: score(row, model)}) for row in split["test"]]
    return {
        "ok": True,
        "version": VERSION,
        "split": {key: value for key, value in split.items() if key not in {"train", "validation", "test"}},
        "model": model,
        "validation": evaluate(validation, probability_key, label_key, baseline_probability_key=baseline_probability_key),
        "test": evaluate(test, probability_key, label_key, baseline_probability_key=baseline_probability_key),
        "testWasUntouchedDuringFitAndSelection": True,
    }
