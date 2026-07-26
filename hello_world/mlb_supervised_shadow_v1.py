"""Nested chronological supervised MLB challenger, strictly shadow-only."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from mlb_supervised_features_v1 import FEATURE_GROUPS, VERSION as FEATURE_VERSION, prepare_examples
from mlb_supervised_model_v1 import (
    MODEL_FAMILY,
    VERSION as MODEL_VERSION,
    _by_dates,
    _expanding_blocks,
    _rank,
    chronological_partitions,
    evaluate,
    fit_calibration,
    fit_logistic,
    market_predictions,
    predictions_for,
)

VERSION = "MLB-SUPERVISED-SHADOW-v1.0-nested-chronological"
L2_VALUES = (0.001, 0.003, 0.01, 0.03, 0.10, 0.30)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _cross_validate(examples, train_dates, feature_names, l2: float) -> Dict[str, Any]:
    rows = []
    fold_metrics = []
    for fold, (fit_dates, validation_dates) in enumerate(_expanding_blocks(train_dates), 1):
        fit_rows = _by_dates(examples, fit_dates)
        validation_rows = _by_dates(examples, validation_dates)
        if not fit_rows or not validation_rows:
            continue
        model = fit_logistic(fit_rows, feature_names, l2)
        predictions = predictions_for(model, validation_rows)
        rows.extend(predictions)
        fold_metrics.append({
            "fold": fold,
            "fitFirstDate": min(fit_dates),
            "fitLastDate": max(fit_dates),
            "validationFirstDate": min(validation_dates),
            "validationLastDate": max(validation_dates),
            "fitGameCount": len(fit_rows),
            "validationGameCount": len(validation_rows),
            "metrics": evaluate(predictions),
        })
    return {"metrics": evaluate(rows), "predictions": rows, "folds": fold_metrics}


def train_and_evaluate(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    examples = prepare_examples(records)
    partitions = chronological_partitions(examples)
    train_rows = _by_dates(examples, partitions["train"])
    walk_rows = _by_dates(examples, partitions["walkForward"])
    holdout_rows = _by_dates(examples, partitions["untouchedHoldout"])
    ablations: Dict[str, Any] = {}
    ranked: List[Tuple[Tuple[float, ...], str, float, Dict[str, Any]]] = []
    for group, names in FEATURE_GROUPS.items():
        group_trials = []
        for l2 in L2_VALUES:
            cv = _cross_validate(examples, partitions["train"], names, l2)
            trial = {
                "l2": l2,
                "featureCount": len(names),
                "metrics": cv["metrics"],
                "folds": cv["folds"],
            }
            group_trials.append(trial)
            ranked.append((_rank(cv["metrics"], len(names)), group, l2, cv))
        best = max(group_trials, key=lambda row: _rank(row["metrics"], row["featureCount"]))
        ablations[group] = {
            "featureNames": list(names),
            "bestL2": best["l2"],
            "nestedChronologicalMetrics": best["metrics"],
            "trials": group_trials,
        }
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, selected_group, selected_l2, selected_cv = ranked[0]
    feature_names = FEATURE_GROUPS[selected_group]
    calibration = fit_calibration(
        [
            (float(row["homeWinProbability"]), int(row["homeWon"]))
            for row in selected_cv["predictions"]
        ]
    )
    development_model = fit_logistic(train_rows, feature_names, selected_l2)
    walk_predictions = predictions_for(development_model, walk_rows, calibration)
    walk_metrics = evaluate(walk_predictions)
    market_walk = evaluate(market_predictions(walk_rows))

    # Freeze selection and calibration before this first and only holdout read.
    frozen_model = fit_logistic(train_rows + walk_rows, feature_names, selected_l2)
    holdout_predictions = predictions_for(frozen_model, holdout_rows, calibration)
    holdout_metrics = evaluate(holdout_predictions)
    market_holdout = evaluate(market_predictions(holdout_rows))

    errors = []
    if walk_metrics["meanDailyAccuracy"] < 0.80:
        errors.append("walk_forward_mean_daily_accuracy_failed")
    if walk_metrics["minimumDailyAccuracy"] < 0.80:
        errors.append("walk_forward_minimum_daily_accuracy_failed")
    if holdout_metrics["meanDailyAccuracy"] < 0.80:
        errors.append("untouched_holdout_mean_daily_accuracy_failed")
    if holdout_metrics["minimumDailyAccuracy"] < 0.80:
        errors.append("untouched_holdout_minimum_daily_accuracy_failed")
    if holdout_metrics["brierScore"] >= market_holdout["brierScore"]:
        errors.append("untouched_holdout_brier_not_better_than_market")
    if holdout_metrics["logLoss"] >= market_holdout["logLoss"]:
        errors.append("untouched_holdout_log_loss_not_better_than_market")
    if holdout_metrics["expectedCalibrationError"] > 0.08:
        errors.append("untouched_holdout_calibration_error_above_0_08")
    if holdout_metrics["overallAccuracy"] < market_holdout["overallAccuracy"] + 0.01:
        errors.append("untouched_holdout_accuracy_lift_below_1pp")

    model_payload = {
        "version": VERSION,
        "modelVersion": MODEL_VERSION,
        "featureVersion": FEATURE_VERSION,
        "modelFamily": MODEL_FAMILY,
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "selectedFeatureGroup": selected_group,
        "selectedL2": selected_l2,
        "model": frozen_model.to_dict(),
        "calibration": calibration.to_dict(),
        "partitions": partitions,
    }
    model_payload["modelDigest"] = _digest(model_payload)
    return {
        "ok": True,
        "status": "SHADOW_PROMOTION_ELIGIBLE" if not errors else "SHADOW_CANDIDATE_REJECTED",
        "version": VERSION,
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "postLockDataExcluded": all(row.get("postLockDataExcluded") is True for row in records),
        "wholeSlateChronologicalPartitions": True,
        "nestedChronologicalModelSelection": True,
        "holdoutUsedOnceAfterModelFreeze": True,
        "marketResidualModel": True,
        "probabilitiesCappedAt": 0.85,
        "recordCount": len(records),
        "exampleCount": len(examples),
        "partitionGameCounts": {
            "train": len(train_rows),
            "walkForward": len(walk_rows),
            "untouchedHoldout": len(holdout_rows),
        },
        "selectedFeatureGroup": selected_group,
        "selectedL2": selected_l2,
        "ablationStudy": ablations,
        "walkForward": walk_metrics,
        "walkForwardMarketBaseline": market_walk,
        "untouchedHoldout": holdout_metrics,
        "untouchedHoldoutMarketBaseline": market_holdout,
        "promotionGate": {
            "version": "MLB-SUPERVISED-SHADOW-PROMOTION-GATE-v1",
            "requiredEveryDayAccuracy": 0.80,
            "requiredCalibrationErrorMaximum": 0.08,
            "requiredAccuracyLiftVsMarket": 0.01,
            "requiresBetterBrierThanMarket": True,
            "requiresBetterLogLossThanMarket": True,
            "passed": not errors,
            "errors": errors,
        },
        "modelArtifact": model_payload,
        "modelDigest": model_payload["modelDigest"],
    }
