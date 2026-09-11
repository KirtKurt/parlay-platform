#!/usr/bin/env python3
"""Read-only MLB reliability calibration audit against immutable candidate artifacts.

The audit never writes model, prediction, ledger, promotion, or authority state.
Calibration is fitted only on earlier validation whole slates.  Candidate
regularization is chosen on a nested holdout inside that earlier window, with
identity as the no-harm fallback.  A reliability threshold is selected only on
later validation whole slates.  The existing, already-reviewed prospective
partition is evaluated only as development information and is explicitly not
prospective qualification evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
HELLO_WORLD = ROOT / "hello_world"
if str(HELLO_WORLD) not in sys.path:
    sys.path.insert(0, str(HELLO_WORLD))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import mlb_ml_dual_model_v2 as dual
import mlb_ml_walk_forward_v2 as walk_forward
import mlb_reliability_calibration_v1 as calibration
import mlb_reliability_calibration_selection_v1 as calibration_selection
from mlb_challenger_benchmark import load_sources


VERSION = "MLB-RELIABILITY-CALIBRATION-SHADOW-AUDIT-v2-no-harm-nested-selection"


def _score_reliability(rows: List[Dict[str, Any]], challenger: Dict[str, Any]) -> List[Dict[str, Any]]:
    model = challenger.get("reliabilityModel") or {}
    if model.get("ok") is not True:
        raise ValueError("verified trained reliability model is required")
    return [
        {**row, "reliabilityProbability": dual.score(row, model)}
        for row in rows
    ]


def _apply_calibrator(
    rows: List[Dict[str, Any]], calibrator: calibration.RegularizedLogitCalibrator
) -> List[Dict[str, Any]]:
    return [
        {
            **row,
            "rawReliabilityProbability": float(row["reliabilityProbability"]),
            "reliabilityProbability": calibrator.apply(row["reliabilityProbability"]),
            "reliabilityCalibrationVersion": calibration.VERSION,
        }
        for row in rows
    ]


def audit(dataset: Dict[str, Any], challenger: Dict[str, Any], provenance: Dict[str, Any]) -> Dict[str, Any]:
    partitions = dataset.get("partitions") or {}
    validation_source = list(partitions.get("validation") or [])
    prospective_source = list(partitions.get("prospectiveTest") or [])
    validation = dual.records_from_clean_rows(validation_source)
    prospective = dual.records_from_clean_rows(prospective_source)
    if len(validation) != len(validation_source) or not validation:
        raise ValueError("validation artifact cannot be reproduced exactly as strict V2 records")
    if len(prospective) != len(prospective_source) or not prospective:
        raise ValueError("prospective artifact cannot be reproduced exactly as strict V2 records")

    scored_validation = _score_reliability(validation, challenger)
    earlier, later, chronology = calibration.split_validation_slates(scored_validation)
    calibrator, calibration_selection_proof = calibration_selection.select_no_harm_calibrator(earlier)
    calibrated_later = _apply_calibrator(later, calibrator)
    threshold = walk_forward.select_reliability_threshold(
        calibrated_later,
        minimum_selected=30,
        minimum_coverage=0.10,
    )
    if threshold.get("ok") is not True:
        raise ValueError("later-validation reliability threshold selection failed")

    scored_prospective = _score_reliability(prospective, challenger)
    calibrated_prospective = _apply_calibrator(scored_prospective, calibrator)
    selected_threshold = float(threshold["threshold"])

    raw_validation_metrics = walk_forward.evaluate(
        later, "reliabilityProbability", "pickCorrect"
    )
    calibrated_validation_metrics = walk_forward.evaluate(
        calibrated_later, "reliabilityProbability", "pickCorrect"
    )
    raw_prospective_metrics = walk_forward.evaluate(
        scored_prospective, "reliabilityProbability", "pickCorrect"
    )
    calibrated_prospective_metrics = walk_forward.evaluate(
        calibrated_prospective, "reliabilityProbability", "pickCorrect"
    )
    raw_selected = dual._selected_reliability_test(
        scored_prospective, selected_threshold
    )
    calibrated_selected = dual._selected_reliability_test(
        calibrated_prospective, selected_threshold
    )

    later_validation_no_harm = bool(
        calibrated_validation_metrics.get("calibrationError") is not None
        and raw_validation_metrics.get("calibrationError") is not None
        and calibrated_validation_metrics["calibrationError"] <= raw_validation_metrics["calibrationError"] + 1e-12
        and calibrated_validation_metrics["brierScore"] <= raw_validation_metrics["brierScore"] + 1e-12
        and calibrated_validation_metrics["logLoss"] <= raw_validation_metrics["logLoss"] + 1e-12
    )

    return {
        "ok": True,
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceExperimentId": dataset.get("experimentId"),
        "sourceProvenance": provenance,
        "calibrationRule": calibration.CALIBRATION_RULE,
        "calibrator": calibrator.to_dict(),
        "calibrationSelectionProof": calibration_selection_proof,
        "chronologyProof": chronology,
        "thresholdSelection": {
            **threshold,
            "selectionSource": "later_validation_only_after_earlier_validation_calibration",
        },
        "laterValidation": {
            "rawReliability": raw_validation_metrics,
            "calibratedReliability": calibrated_validation_metrics,
            "noHarmAcrossCalibrationBrierAndLogLoss": later_validation_no_harm,
        },
        "reviewedProspectiveDevelopmentOnly": {
            "rawReliability": raw_prospective_metrics,
            "calibratedReliability": calibrated_prospective_metrics,
            "rawSelectedReliabilityAtCalibratedThreshold": raw_selected,
            "calibratedSelectedReliability": calibrated_selected,
            "isFutureQualificationEvidence": False,
            "reason": "existing prospective outcomes were already reviewed before this calibration challenger was created",
        },
        "calibrationChallengerReadyForNewFutureShadow": later_validation_no_harm,
        "prospectiveQualificationEvidence": False,
        "promotionEligible": False,
        "productionAuthorityChanged": False,
        "immutablePredictionRewriteAllowed": False,
        "immutableHistoryRewritten": False,
        "selectionLedgerRewritten": False,
        "automaticPromotionEnabled": False,
        "automaticWagerAllowed": False,
        "nextRequiredEvidence": (
            "only if later-validation no-harm proof passes, create a separately versioned shadow challenger and "
            "accumulate new pre-outcome selections; never reuse these reviewed prospective outcomes for promotion"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="parlay-platform-dev")
    parser.add_argument(
        "--output",
        default="runtime_reports/mlb_reliability_calibration_shadow_audit_latest.json",
    )
    args = parser.parse_args()
    sources, provenance = load_sources(args.stack)
    report = audit(sources["dataset"], sources["frozenChallenger"], provenance)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
