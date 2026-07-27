"""Selective individual-game objective for the V7 historical odds learner.

The module preserves V7's immutable odds-only inputs and chronological partitions,
but evaluates a frozen PICK/PASS threshold instead of requiring a prediction for
every game.  Threshold selection uses development/walk-forward evidence only; the
untouched holdout is evaluated once after the threshold is frozen.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-SELECTIVE-OBJECTIVE-v1"
THRESHOLDS = (0.60, 0.625, 0.65, 0.675, 0.70, 0.725, 0.75, 0.775, 0.80)
MIN_WALK_FORWARD_PICKS = 200
MIN_UNTOUCHED_PICKS = 200
MIN_SELECTION_DAYS = 50
MIN_COVERAGE = 0.05
PRODUCTION_ACCURACY = 0.75
ELITE_ACCURACY = 0.80


def _evaluate(
    optimizer: Any,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    dates: Iterable[str],
    threshold: float,
) -> Dict[str, Any]:
    date_set = {str(value) for value in dates}
    rows = [row for row in records if str(row.get("slateDateEt") or "") in date_set]
    predictions = [optimizer.predict_record(row, policy) for row in rows]
    selected = []
    for prediction in predictions:
        home_probability = float(prediction.get("homeWinProbability") or 0.5)
        confidence = max(home_probability, 1.0 - home_probability)
        if confidence + 1e-12 < threshold:
            continue
        item = dict(prediction)
        item["selectionConfidence"] = confidence
        item["decision"] = "PICK"
        selected.append(item)

    by_day: Dict[str, list] = {}
    for row in selected:
        by_day.setdefault(str(row.get("slateDateEt") or ""), []).append(row)
    selected_days = sorted(by_day)
    correct = sum(row.get("correct") is True for row in selected)
    pick_count = len(selected)
    accuracy = correct / pick_count if pick_count else 0.0
    coverage = pick_count / len(predictions) if predictions else 0.0
    daily = []
    for day in selected_days:
        day_rows = by_day[day]
        day_correct = sum(row.get("correct") is True for row in day_rows)
        daily.append(
            {
                "slateDateEt": day,
                "pickCount": len(day_rows),
                "correct": day_correct,
                "accuracy": day_correct / len(day_rows),
            }
        )
    return {
        "threshold": threshold,
        "eligibleGameCount": len(predictions),
        "pickCount": pick_count,
        "passCount": max(0, len(predictions) - pick_count),
        "selectionDayCount": len(selected_days),
        "correct": correct,
        "accuracy": accuracy,
        "coverage": coverage,
        "minimumDailyAccuracy": min((row["accuracy"] for row in daily), default=0.0),
        "meanDailyAccuracy": (
            sum(row["accuracy"] for row in daily) / len(daily) if daily else 0.0
        ),
        "daily": daily,
    }


def _rank(metrics: Mapping[str, Any]) -> tuple:
    sample_ok = int(
        int(metrics.get("pickCount") or 0) >= MIN_WALK_FORWARD_PICKS
        and int(metrics.get("selectionDayCount") or 0) >= MIN_SELECTION_DAYS
        and float(metrics.get("coverage") or 0.0) >= MIN_COVERAGE
    )
    return (
        sample_ok,
        float(metrics.get("accuracy") or 0.0),
        int(metrics.get("pickCount") or 0),
        float(metrics.get("coverage") or 0.0),
    )


def evaluate_search_result(
    optimizer: Any,
    records: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    output = copy.deepcopy(dict(result))
    partitions = output.get("partitions") or {}
    candidate = output.get("candidate") or {}
    baseline = output.get("baseline") or {}
    candidate_policy = candidate.get("policy") or {}
    baseline_policy = baseline.get("policy") or {}
    if not candidate_policy or not partitions.get("walkForward") or not partitions.get("untouchedHoldout"):
        output["selectiveObjective"] = {
            "version": VERSION,
            "status": "INSUFFICIENT_CANONICAL_SEARCH_RESULT",
            "promotionAuthority": False,
        }
        return output

    walk_forward_candidates = [
        _evaluate(optimizer, records, candidate_policy, partitions["walkForward"], threshold)
        for threshold in THRESHOLDS
    ]
    frozen_walk_forward = max(walk_forward_candidates, key=_rank)
    frozen_threshold = float(frozen_walk_forward["threshold"])
    untouched = _evaluate(
        optimizer,
        records,
        candidate_policy,
        partitions["untouchedHoldout"],
        frozen_threshold,
    )
    baseline_walk_forward = _evaluate(
        optimizer,
        records,
        baseline_policy,
        partitions["walkForward"],
        frozen_threshold,
    )
    errors = []
    if frozen_walk_forward["pickCount"] < MIN_WALK_FORWARD_PICKS:
        errors.append("walk_forward_selective_pick_floor_not_met")
    if untouched["pickCount"] < MIN_UNTOUCHED_PICKS:
        errors.append("untouched_selective_pick_floor_not_met")
    if frozen_walk_forward["selectionDayCount"] < MIN_SELECTION_DAYS:
        errors.append("walk_forward_selection_day_floor_not_met")
    if untouched["selectionDayCount"] < MIN_SELECTION_DAYS:
        errors.append("untouched_selection_day_floor_not_met")
    if frozen_walk_forward["coverage"] + 1e-12 < MIN_COVERAGE:
        errors.append("walk_forward_selective_coverage_floor_not_met")
    if untouched["coverage"] + 1e-12 < MIN_COVERAGE:
        errors.append("untouched_selective_coverage_floor_not_met")
    if frozen_walk_forward["accuracy"] + 1e-12 < PRODUCTION_ACCURACY:
        errors.append("walk_forward_selective_accuracy_failed")
    if untouched["accuracy"] + 1e-12 < PRODUCTION_ACCURACY:
        errors.append("untouched_selective_accuracy_failed")
    if frozen_walk_forward["accuracy"] <= baseline_walk_forward["accuracy"] + 1e-12:
        errors.append("candidate_did_not_improve_selective_accuracy")

    output["objective"] = "selective_individual_game_accuracy"
    output["selectiveObjective"] = {
        "version": VERSION,
        "status": "SELECTIVE_PROMOTION_GATE_PASSED" if not errors else "SELECTIVE_CANDIDATE_REJECTED",
        "promotionAuthority": False,
        "pickPassEnabled": True,
        "thresholdFrozenBeforeUntouchedHoldout": True,
        "thresholdSelectionUsedHoldoutLabels": False,
        "frozenThreshold": frozen_threshold,
        "walkForward": frozen_walk_forward,
        "untouchedHoldout": untouched,
        "baselineWalkForwardAtFrozenThreshold": baseline_walk_forward,
        "requirements": {
            "minimumWalkForwardPicks": MIN_WALK_FORWARD_PICKS,
            "minimumUntouchedPicks": MIN_UNTOUCHED_PICKS,
            "minimumSelectionDays": MIN_SELECTION_DAYS,
            "minimumCoverage": MIN_COVERAGE,
            "productionAccuracy": PRODUCTION_ACCURACY,
            "eliteAccuracy": ELITE_ACCURACY,
        },
        "productionGatePassed": not errors,
        "eliteGatePassed": bool(
            not errors
            and frozen_walk_forward["accuracy"] + 1e-12 >= ELITE_ACCURACY
            and untouched["accuracy"] + 1e-12 >= ELITE_ACCURACY
        ),
        "errors": sorted(set(errors)),
    }
    return output


def install(optimizer: Any) -> None:
    if getattr(optimizer, "_INQSI_V7_SELECTIVE_OBJECTIVE_INSTALLED", False):
        return
    original_search = optimizer.search

    def search(records, config=None, *, untouched_holdout_dates=None):
        result = original_search(
            records,
            config,
            untouched_holdout_dates=untouched_holdout_dates,
        )
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            return result
        return evaluate_search_result(optimizer, records, result)

    optimizer.search = search
    optimizer.SELECTIVE_OBJECTIVE_VERSION = VERSION
    optimizer._INQSI_V7_SELECTIVE_OBJECTIVE_INSTALLED = True
