"""Chronology-safe calibration helper for the MLB reliability shadow challenger.

This module is intentionally non-authoritative.  It fits a regularized logit
adjustment only on the earlier portion of validation slates, applies that fixed
calibrator to later validation slates, and leaves all prospective observations
untouched.  A caller may use the later validation rows for threshold selection
without reusing the rows that fitted calibration.

Nothing in this module writes predictions, rewrites immutable evidence, changes
promotion thresholds, promotes a model, or grants production/wager authority.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


VERSION = "MLB-RELIABILITY-CALIBRATION-v1-earlier-validation-only-regularized-logit"
CALIBRATION_RULE = "earlier_validation_only_regularized_logit_adjustment"
DEFAULT_L2 = 0.10
DEFAULT_STEPS = 600
PROBABILITY_FLOOR = 1e-6


def _clip_probability(value: Any) -> float:
    probability = float(value)
    if not math.isfinite(probability):
        raise ValueError("reliability probability must be finite")
    if not 0.0 < probability < 1.0:
        raise ValueError("reliability probability must be strictly between zero and one")
    return min(1.0 - PROBABILITY_FLOOR, max(PROBABILITY_FLOOR, probability))


def _logit(probability: Any) -> float:
    p = _clip_probability(probability)
    return math.log(p) - math.log1p(-p)


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _outcome(value: Any) -> int:
    if value in (True, 1, 1.0, "1"):
        return 1
    if value in (False, 0, 0.0, "0"):
        return 0
    raise ValueError("pickCorrect must be a binary final-settlement label")


def _date(row: Mapping[str, Any]) -> str:
    value = str(row.get("slateDateEt") or "").strip()
    if not value:
        raise ValueError("slateDateEt is required for whole-slate chronology")
    return value


def split_validation_slates(
    validation_rows: Iterable[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Split validation by whole slate date: earlier fit, later selection.

    The split is deterministic and date-ordered.  No slate can appear on both
    sides.  At least two distinct slate dates are required because calibration
    and threshold selection must not reuse the same observations.
    """
    rows = [dict(row) for row in validation_rows]
    dates = sorted({_date(row) for row in rows})
    if len(dates) < 2:
        raise ValueError("at least two validation slate dates are required")
    split_index = max(1, len(dates) // 2)
    if split_index >= len(dates):
        split_index = len(dates) - 1
    calibration_dates = tuple(dates[:split_index])
    selection_dates = tuple(dates[split_index:])
    calibration_set = set(calibration_dates)
    selection_set = set(selection_dates)
    if calibration_set & selection_set:
        raise ValueError("validation slate split overlap")
    if max(calibration_dates) >= min(selection_dates):
        raise ValueError("calibration slates must strictly precede threshold-selection slates")
    calibration_rows = [row for row in rows if _date(row) in calibration_set]
    selection_rows = [row for row in rows if _date(row) in selection_set]
    if not calibration_rows or not selection_rows:
        raise ValueError("both validation calibration and selection rows are required")
    proof = {
        "version": VERSION,
        "calibrationRule": CALIBRATION_RULE,
        "calibrationSlateDates": list(calibration_dates),
        "thresholdSelectionSlateDates": list(selection_dates),
        "calibrationRowCount": len(calibration_rows),
        "thresholdSelectionRowCount": len(selection_rows),
        "wholeSlatesKeptTogether": True,
        "calibrationStrictlyEarlierThanThresholdSelection": True,
        "prospectiveRowsUsedForCalibration": 0,
        "prospectiveRowsUsedForThresholdSelection": 0,
    }
    return calibration_rows, selection_rows, proof


@dataclass(frozen=True)
class RegularizedLogitCalibrator:
    slope: float
    intercept: float
    l2: float
    training_count: int
    positive_count: int
    negative_count: int

    def apply(self, probability: Any) -> float:
        calibrated = _sigmoid(self.slope * _logit(probability) + self.intercept)
        return min(1.0 - PROBABILITY_FLOOR, max(PROBABILITY_FLOOR, calibrated))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": VERSION,
            "calibrationRule": CALIBRATION_RULE,
            "slope": self.slope,
            "intercept": self.intercept,
            "l2": self.l2,
            "trainingCount": self.training_count,
            "positiveCount": self.positive_count,
            "negativeCount": self.negative_count,
            "fitSource": "earlier_validation_only",
        }


def fit_regularized_logit(
    rows: Sequence[Mapping[str, Any]],
    *,
    probability_key: str = "reliabilityProbability",
    label_key: str = "pickCorrect",
    l2: float = DEFAULT_L2,
    steps: int = DEFAULT_STEPS,
) -> RegularizedLogitCalibrator:
    """Fit a deterministic Platt-style adjustment with shrinkage to identity."""
    if not rows:
        raise ValueError("calibration rows are required")
    if not math.isfinite(float(l2)) or float(l2) <= 0.0:
        raise ValueError("positive finite calibration regularization is required")
    if int(steps) < 1:
        raise ValueError("positive calibration optimization steps are required")
    logits = [_logit(row.get(probability_key)) for row in rows]
    outcomes = [_outcome(row.get(label_key)) for row in rows]
    positives = sum(outcomes)
    negatives = len(outcomes) - positives
    if not positives or not negatives:
        raise ValueError("both settled outcomes are required to fit calibration")

    # Start at identity so small samples are explicitly shrunk toward the raw
    # reliability model rather than allowed to make an unstable large rewrite.
    slope = 1.0
    intercept = 0.0
    rate = 0.08
    regularization = float(l2)
    count = float(len(rows))
    for step in range(1, int(steps) + 1):
        grad_slope = 0.0
        grad_intercept = 0.0
        for x, y in zip(logits, outcomes):
            error = _sigmoid(slope * x + intercept) - y
            grad_slope += error * x
            grad_intercept += error
        # Penalize distance from identity (slope=1, intercept=0), preserving the
        # raw model as the small-sample prior rather than shrinking to a flat 0.5.
        grad_slope = grad_slope / count + regularization * (slope - 1.0)
        grad_intercept = grad_intercept / count + regularization * intercept
        learning_rate = rate / math.sqrt(step)
        slope -= learning_rate * grad_slope
        intercept -= learning_rate * grad_intercept
        slope = max(0.10, min(4.0, slope))
        intercept = max(-3.0, min(3.0, intercept))

    return RegularizedLogitCalibrator(
        slope=round(slope, 12),
        intercept=round(intercept, 12),
        l2=regularization,
        training_count=len(rows),
        positive_count=positives,
        negative_count=negatives,
    )


def _metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    probability_key: str,
    label_key: str = "pickCorrect",
    bins: int = 10,
) -> Dict[str, Any]:
    if not rows:
        return {"count": 0, "brierScore": None, "logLoss": None, "calibrationError": None}
    probabilities = [_clip_probability(row.get(probability_key)) for row in rows]
    outcomes = [_outcome(row.get(label_key)) for row in rows]
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(rows)
    log_loss = sum(
        -(y * math.log(p) + (1 - y) * math.log1p(-p))
        for p, y in zip(probabilities, outcomes)
    ) / len(rows)
    calibration_error = 0.0
    bin_rows: List[Dict[str, Any]] = []
    for index in range(int(bins)):
        low = index / bins
        high = (index + 1) / bins
        selected = [
            (p, y)
            for p, y in zip(probabilities, outcomes)
            if p >= low and (p < high or index == bins - 1)
        ]
        if not selected:
            continue
        mean_probability = sum(p for p, _ in selected) / len(selected)
        observed_rate = sum(y for _, y in selected) / len(selected)
        absolute_error = abs(mean_probability - observed_rate)
        calibration_error += len(selected) / len(rows) * absolute_error
        bin_rows.append(
            {
                "lower": low,
                "upper": high,
                "count": len(selected),
                "meanProbability": round(mean_probability, 10),
                "observedCorrectRate": round(observed_rate, 10),
                "absoluteError": round(absolute_error, 10),
            }
        )
    return {
        "count": len(rows),
        "brierScore": round(brier, 10),
        "logLoss": round(log_loss, 10),
        "calibrationError": round(calibration_error, 10),
        "calibrationBins": bin_rows,
    }


def prepare_validation_for_threshold_selection(
    validation_rows: Iterable[Mapping[str, Any]],
    *,
    probability_key: str = "reliabilityProbability",
    label_key: str = "pickCorrect",
    l2: float = DEFAULT_L2,
) -> Dict[str, Any]:
    """Fit on earlier validation and return calibrated later validation rows.

    The returned rows are copies.  Raw probabilities are retained alongside the
    calibrated probability so diagnostics can compare both without mutating any
    immutable source record.
    """
    calibration_rows, selection_rows, chronology = split_validation_slates(validation_rows)
    calibrator = fit_regularized_logit(
        calibration_rows,
        probability_key=probability_key,
        label_key=label_key,
        l2=l2,
    )
    calibrated_selection: List[Dict[str, Any]] = []
    for source in selection_rows:
        raw = _clip_probability(source.get(probability_key))
        calibrated_selection.append(
            {
                **source,
                "rawReliabilityProbability": raw,
                probability_key: calibrator.apply(raw),
                "reliabilityCalibrationVersion": VERSION,
            }
        )
    raw_selection = [
        {**row, "rawReliabilityProbability": _clip_probability(row.get(probability_key))}
        for row in selection_rows
    ]
    raw_metrics_rows = [
        {**row, "metricProbability": row["rawReliabilityProbability"]}
        for row in raw_selection
    ]
    calibrated_metrics_rows = [
        {**row, "metricProbability": row[probability_key]}
        for row in calibrated_selection
    ]
    return {
        "ok": True,
        "version": VERSION,
        "calibrationRule": CALIBRATION_RULE,
        "calibrator": calibrator.to_dict(),
        "chronologyProof": chronology,
        "thresholdSelectionRows": calibrated_selection,
        "diagnostics": {
            "rawLaterValidation": _metrics(raw_metrics_rows, probability_key="metricProbability", label_key=label_key),
            "calibratedLaterValidation": _metrics(
                calibrated_metrics_rows, probability_key="metricProbability", label_key=label_key
            ),
        },
        "prospectiveQualificationEvidence": False,
        "promotionEligible": False,
        "productionAuthorityChanged": False,
        "immutableHistoryRewritten": False,
        "automaticWagerAllowed": False,
    }
