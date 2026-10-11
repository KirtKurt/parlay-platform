from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .ml import Model


def model_threshold(model: Any, fallback: float) -> float:
    metadata = getattr(model, 'metadata', None) or {}
    try:
        value = float(metadata.get('official_probability_threshold', fallback))
    except Exception:
        value = float(fallback)
    return max(.50, min(.95, value))


def qualifies(model: Any, win_probability: float, fallback: float) -> bool:
    if model is None:
        return False
    return float(win_probability) >= model_threshold(model, fallback)


def _wilson_lower(correct: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = correct / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = p + z2 / (2.0 * total)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def evaluate_threshold(
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    threshold: float,
) -> dict[str, Any]:
    selected: list[int] = []
    for row, label in zip(rows, labels):
        probability = float(model.predict(row))
        confidence = max(probability, 1.0 - probability)
        if confidence >= threshold:
            selected.append(int((probability >= .5) == bool(int(label))))
    count = len(selected)
    correct = sum(selected)
    return {
        'threshold': float(threshold),
        'selection_count': count,
        'selection_correct': correct,
        'selection_accuracy': (correct / count) if count else None,
        'selection_coverage': count / max(1, len(rows)),
        'selection_wilson_lower_bound': _wilson_lower(correct, count),
    }


def learn_threshold(
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    fallback: float,
) -> tuple[float, dict[str, Any]]:
    fallback_value = max(.50, min(.95, float(fallback)))
    if not callable(getattr(model, 'predict', None)) or not rows or len(rows) != len(labels):
        return fallback_value, {
            'threshold': fallback_value,
            'threshold_source': 'FALLBACK_NO_VALIDATION_SIGNAL',
            'selection_count': 0,
            'selection_accuracy': None,
            'selection_coverage': 0.0,
            'selection_wilson_lower_bound': 0.0,
        }

    minimum_selected = max(10, min(25, len(rows) // 4))
    best: tuple[tuple[float, float, int, float], dict[str, Any]] | None = None
    for step in range(50, 96):
        threshold = step / 100.0
        summary = evaluate_threshold(model, rows, labels, threshold)
        count = int(summary['selection_count'])
        accuracy = summary['selection_accuracy']
        if count < minimum_selected or accuracy is None:
            continue
        score = (
            float(summary['selection_wilson_lower_bound']),
            float(accuracy),
            count,
            threshold,
        )
        if best is None or score > best[0]:
            best = (score, summary)

    if best is None:
        summary = evaluate_threshold(model, rows, labels, fallback_value)
        return fallback_value, {
            **summary,
            'threshold_source': 'FALLBACK_INSUFFICIENT_SELECTED_VALIDATION',
            'minimum_selected_validation': minimum_selected,
        }

    threshold = float(best[1]['threshold'])
    return threshold, {
        **best[1],
        'threshold_source': 'AUTONOMOUS_CHRONOLOGICAL_VALIDATION',
        'minimum_selected_validation': minimum_selected,
        'selection_policy': 'ACCURACY_WILSON_NO_ROI_V1',
    }


def attach_learned_threshold(
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    fallback: float,
) -> tuple[Any, dict[str, Any]]:
    threshold, metrics = learn_threshold(model, rows, labels, fallback)
    if not isinstance(model, Model):
        return model, metrics
    metadata = dict(model.metadata or {})
    metadata.update({
        'official_probability_threshold': threshold,
        'official_threshold_source': metrics.get('threshold_source'),
        'official_threshold_policy': metrics.get('selection_policy', 'FALLBACK'),
        'official_threshold_validation_count': metrics.get('selection_count'),
        'official_threshold_validation_accuracy': metrics.get('selection_accuracy'),
        'official_threshold_validation_wilson_lower_bound': metrics.get('selection_wilson_lower_bound'),
    })
    return Model(
        model.intercept,
        model.weights,
        model.feature_names,
        model.model_family,
        metadata,
    ), metrics


def evaluate_model_threshold(
    model: Any,
    rows: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    fallback: float,
) -> dict[str, Any]:
    if not callable(getattr(model, 'predict', None)):
        return {
            'threshold': model_threshold(model, fallback),
            'selection_count': 0,
            'selection_accuracy': None,
            'selection_coverage': 0.0,
            'selection_wilson_lower_bound': 0.0,
        }
    return evaluate_threshold(model, rows, labels, model_threshold(model, fallback))
