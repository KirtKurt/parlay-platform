"""Nested no-harm selector for MLB reliability calibration research.

Candidate regularization is chosen entirely inside the earlier validation
window.  The later validation window remains untouched for reliability
threshold selection.  Identity is the fail-closed fallback whenever no
calibrator improves calibration without also preserving both Brier score and
log loss on the nested holdout.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import mlb_reliability_calibration_v1 as calibration


VERSION = "MLB-RELIABILITY-CALIBRATION-SELECTION-v1-nested-earlier-validation-no-harm"
CANDIDATE_L2 = (0.01, 0.10, 1.0)


def _metrics(rows: Sequence[Mapping[str, Any]], probability_key: str) -> Dict[str, Any]:
    return calibration._metrics(rows, probability_key=probability_key, label_key="pickCorrect")


def _calibrated_rows(
    rows: Sequence[Mapping[str, Any]],
    calibrator: calibration.RegularizedLogitCalibrator,
) -> List[Dict[str, Any]]:
    return [
        {
            **row,
            "candidateCalibrationProbability": calibrator.apply(row["reliabilityProbability"]),
        }
        for row in rows
    ]


def _identity(rows: Sequence[Mapping[str, Any]]) -> calibration.RegularizedLogitCalibrator:
    labels = [calibration._outcome(row.get("pickCorrect")) for row in rows]
    return calibration.RegularizedLogitCalibrator(
        slope=1.0,
        intercept=0.0,
        l2=0.0,
        training_count=len(rows),
        positive_count=sum(labels),
        negative_count=len(labels) - sum(labels),
    )


def select_no_harm_calibrator(
    earlier_validation_rows: Iterable[Mapping[str, Any]],
    *,
    candidate_l2: Tuple[float, ...] = CANDIDATE_L2,
) -> Tuple[calibration.RegularizedLogitCalibrator, Dict[str, Any]]:
    """Choose L2 using a nested later slice of earlier validation only.

    Eligibility is deliberately strict: calibration error must improve and both
    proper scores must be no worse than identity on the nested holdout.  If no
    candidate satisfies all three checks, or the nested fit contains only one
    settled outcome class, identity is returned fail-closed.
    """
    rows = [dict(row) for row in earlier_validation_rows]
    inner_fit, inner_check, chronology = calibration.split_validation_slates(rows)
    raw_metrics = _metrics(inner_check, "reliabilityProbability")
    inner_fit_classes = {
        calibration._outcome(row.get("pickCorrect"))
        for row in inner_fit
    }
    single_class_inner_fit = len(inner_fit_classes) < 2
    candidates: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []
    if not single_class_inner_fit:
        for l2 in candidate_l2:
            fitted = calibration.fit_regularized_logit(inner_fit, l2=float(l2))
            candidate_rows = _calibrated_rows(inner_check, fitted)
            candidate_metrics = _metrics(candidate_rows, "candidateCalibrationProbability")
            no_harm = bool(
                candidate_metrics["calibrationError"] < raw_metrics["calibrationError"]
                and candidate_metrics["brierScore"] <= raw_metrics["brierScore"] + 1e-12
                and candidate_metrics["logLoss"] <= raw_metrics["logLoss"] + 1e-12
            )
            result = {
                "l2": float(l2),
                "calibrator": fitted.to_dict(),
                "holdoutMetrics": candidate_metrics,
                "improvesCalibration": candidate_metrics["calibrationError"] < raw_metrics["calibrationError"],
                "brierNoWorse": candidate_metrics["brierScore"] <= raw_metrics["brierScore"] + 1e-12,
                "logLossNoWorse": candidate_metrics["logLoss"] <= raw_metrics["logLoss"] + 1e-12,
                "eligible": no_harm,
            }
            candidates.append(result)
            if no_harm:
                eligible.append(result)

    if eligible:
        chosen = min(
            eligible,
            key=lambda item: (
                item["holdoutMetrics"]["calibrationError"],
                item["holdoutMetrics"]["brierScore"],
                item["holdoutMetrics"]["logLoss"],
                item["l2"],
            ),
        )
        final = calibration.fit_regularized_logit(rows, l2=chosen["l2"])
        strategy = "regularized_logit"
        selected_l2 = chosen["l2"]
    else:
        final = _identity(rows)
        strategy = "identity_no_harm_fallback"
        selected_l2 = None

    proof = {
        "ok": True,
        "version": VERSION,
        "selectionSource": "nested_earlier_validation_only",
        "calibrationRule": calibration.CALIBRATION_RULE,
        "rawNestedHoldoutMetrics": raw_metrics,
        "candidates": candidates,
        "selectedStrategy": strategy,
        "selectedL2": selected_l2,
        "fallbackReason": "single_class_nested_fit" if single_class_inner_fit else None,
        "innerChronologyProof": chronology,
        "fitRowCountAfterSelection": len(rows),
        "laterValidationUsedForCalibrationSelection": False,
        "prospectiveRowsUsedForCalibrationSelection": 0,
        "prospectiveQualificationEvidence": False,
        "promotionEligible": False,
        "productionAuthorityChanged": False,
    }
    return final, proof
