"""Durable diagnostic selected-pick bands for V7 selective search.

The diagnostic is reporting-only. It reuses the already-frozen V7 policy,
calibration temperature, reliability profile, and chronological partitions.
It never changes threshold selection, candidate ranking, promotion authority,
or production behavior.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Sequence

VERSION = "MLB-HISTORICAL-V7-SELECTED-PICK-BANDS-v1"
BAND_EDGES = (0.60, 0.625, 0.65, 0.675, 0.70, 0.725, 0.75, 0.775, 0.80)


def _summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    picks = len(rows)
    correct = sum((row.get("prediction") or {}).get("correct") is True for row in rows)
    by_day: Dict[str, list] = {}
    for row in rows:
        record = row.get("record") or {}
        by_day.setdefault(str(record.get("slateDateEt") or ""), []).append(row)
    daily = []
    for day in sorted(by_day):
        values = by_day[day]
        day_correct = sum((row.get("prediction") or {}).get("correct") is True for row in values)
        daily.append(
            {
                "slateDateEt": day,
                "pickCount": len(values),
                "correct": day_correct,
                "accuracy": day_correct / len(values),
            }
        )
    return {
        "pickCount": picks,
        "correct": correct,
        "accuracy": correct / picks if picks else 0.0,
        "selectionDayCount": len(by_day),
        "meanDailyAccuracy": sum(row["accuracy"] for row in daily) / len(daily) if daily else 0.0,
        "minimumDailyAccuracy": min((row["accuracy"] for row in daily), default=0.0),
        "daily": daily,
    }


def _reliable_rows(search_module: Any, prepared: Sequence[Mapping[str, Any]], profile_name: str) -> list:
    profile = search_module.RELIABILITY_PROFILES[profile_name]
    output = []
    for item in prepared:
        ok, _ = search_module._reliable(item["record"], item["prediction"], profile)
        if ok:
            output.append(item)
    return output


def _partition_diagnostics(search_module: Any, prepared: Sequence[Mapping[str, Any]], profile_name: str) -> Dict[str, Any]:
    reliable = _reliable_rows(search_module, prepared, profile_name)
    total = len(prepared)
    incremental = []
    for index, lower in enumerate(BAND_EDGES):
        upper = BAND_EDGES[index + 1] if index + 1 < len(BAND_EDGES) else None
        selected = [
            row for row in reliable
            if float(row.get("confidence") or 0.0) + 1e-12 >= lower
            and (upper is None or float(row.get("confidence") or 0.0) < upper - 1e-12)
        ]
        values = _summary(selected)
        values.update(
            {
                "bandType": "incremental",
                "label": f"{lower:.3f}-{upper:.3f}" if upper is not None else f">={lower:.3f}",
                "lowerBound": lower,
                "upperBoundExclusive": upper,
                "eligibleGameCount": total,
                "coverage": len(selected) / total if total else 0.0,
            }
        )
        incremental.append(values)

    cumulative = []
    for threshold in BAND_EDGES:
        selected = [row for row in reliable if float(row.get("confidence") or 0.0) + 1e-12 >= threshold]
        values = _summary(selected)
        values.update(
            {
                "bandType": "cumulative",
                "label": f">={threshold:.3f}",
                "threshold": threshold,
                "eligibleGameCount": total,
                "coverage": len(selected) / total if total else 0.0,
            }
        )
        cumulative.append(values)

    return {
        "eligibleGameCount": total,
        "reliableGameCount": len(reliable),
        "incrementalBands": incremental,
        "cumulativeThresholds": cumulative,
    }


def attach(search_module: Any, optimizer: Any, records: Sequence[Mapping[str, Any]], result: Mapping[str, Any]) -> Dict[str, Any]:
    output = copy.deepcopy(dict(result))
    partitions = output.get("partitions") or {}
    policy = output.get("frozenPolicy") or {}
    temperature = output.get("frozenTemperature")
    profile = output.get("frozenReliabilityProfile")
    if not policy or temperature is None or not profile or not partitions.get("walkForward") or not partitions.get("untouchedHoldout"):
        output["selectedPickBandDiagnostics"] = {
            "version": VERSION,
            "status": "INSUFFICIENT_FROZEN_SELECTIVE_RESULT",
            "reportingOnly": True,
        }
        return output

    walk_forward_rows = search_module._prepared(
        optimizer, records, policy, partitions["walkForward"], float(temperature)
    )
    untouched_rows = search_module._prepared(
        optimizer, records, policy, partitions["untouchedHoldout"], float(temperature)
    )
    output["selectedPickBandDiagnostics"] = {
        "version": VERSION,
        "status": "AVAILABLE",
        "reportingOnly": True,
        "changesPromotionDecision": False,
        "usesFrozenPolicy": True,
        "usesFrozenTemperature": True,
        "usesFrozenReliabilityProfile": True,
        "thresholdSelectionUsedHoldoutLabels": False,
        "bandEdges": list(BAND_EDGES),
        "walkForward": _partition_diagnostics(search_module, walk_forward_rows, str(profile)),
        "untouchedHoldout": _partition_diagnostics(search_module, untouched_rows, str(profile)),
    }
    return output


def install(search_module: Any, optimizer: Any) -> None:
    if getattr(optimizer, "_INQSI_V7_SELECTED_PICK_BANDS_INSTALLED", False):
        return
    original = optimizer.v7_selective_search

    def wrapped(records, config=None, untouched_holdout_dates=None):
        result = original(records, config=config, untouched_holdout_dates=untouched_holdout_dates)
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            return result
        return attach(search_module, optimizer, records, result)

    optimizer.v7_selective_search = wrapped
    optimizer.V7_SELECTED_PICK_BANDS_VERSION = VERSION
    optimizer._INQSI_V7_SELECTED_PICK_BANDS_INSTALLED = True
