from __future__ import annotations

import csv
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from mlb_ml_feature_vector import ML_FEATURES, VERSION as ML_FEATURE_VECTOR_VERSION, feature_vector

NO_PICK_TAGS = {"NO_PICK", "NO_PICK_DISCIPLINE"}
NO_PICK_ACTIONABILITIES = {"PASS_NO_PICK", "NO_PICK", "NO_ACTIONABLE_PICK"}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _is_actionable(row: Dict[str, Any]) -> bool:
    return bool(row.get("actionablePick") is True or row.get("officialPick") is True or row.get("accuracyTargetEligible") is True)


def _is_no_pick(row: Dict[str, Any]) -> bool:
    tags = set(str(x) for x in (row.get("tags") or []))
    actionability = str(row.get("actionability") or "").upper()
    stack = row.get("winnerStackV2") or {}
    discipline = stack.get("discipline") if isinstance(stack, dict) else {}
    discipline_actionability = str((discipline or {}).get("actionability") or "").upper()
    return bool(
        tags & NO_PICK_TAGS
        or actionability in NO_PICK_ACTIONABILITIES
        or discipline_actionability in NO_PICK_ACTIONABILITIES
    )


def _is_optimized(row: Dict[str, Any]) -> bool:
    if _is_no_pick(row) and not _is_actionable(row):
        return False
    winner_optimizer = row.get("winnerOptimizer") or {}
    winner_stack = row.get("winnerStackV2") or {}
    return bool(row.get("individualWinnerOptimized") or winner_optimizer.get("applied") or winner_stack.get("applied"))


def _accuracy(rows: List[Dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get("correct")) / len(rows) * 100.0, 2)


def _prediction_quality(pred: Dict[str, Any]) -> Tuple[int, str]:
    winner_stack = pred.get("winnerStackV2") or {}
    winner_optimizer = pred.get("winnerOptimizer") or {}
    quality = 0
    if winner_stack.get("applied"):
        quality += 1000
    if pred.get("finalGateStored") or pred.get("fullDataFinalPick"):
        quality += 500
    if pred.get("officialPrediction"):
        quality += 250
    if pred.get("actionability"):
        quality += 150
    if pred.get("pickDiscipline"):
        quality += 100
    if winner_optimizer.get("applied") or pred.get("individualWinnerOptimized"):
        quality += 75
    return quality, str(pred.get("createdAt") or pred.get("created_at") or "")


def _dataset(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows or []:
        if row.get("status") != "GRADED" or row.get("correct") is None:
            continue
        rec = {
            "sport": "mlb",
            "slateDate": row.get("slateDateEt"),
            "commenceTime": row.get("commenceTime"),
            "matchup": row.get("matchup"),
            "predictedWinner": row.get("predictedWinner"),
            "winner": row.get("winner"),
            "label": 1 if row.get("correct") else 0,
        }
        rec.update(feature_vector(row))
        out.append(rec)
    return out


def _dedupe_training_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if row.get("status") != "GRADED" or row.get("correct") is None:
            continue
        key = "|".join([
            str(row.get("id") or ""),
            str(row.get("gameKeyBase") or ""),
            str(row.get("commenceTime") or ""),
            str(row.get("predictedWinner") or ""),
        ])
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def _sigmoid(z: float) -> float:
    if z >= 35:
        return 1.0
    if z <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _train(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = sorted(records, key=lambda r: str(r.get("commenceTime") or ""))
    min_rows = int(os.environ.get("INQSI_MLB_ML_MIN_ROWS", "40"))
    target = float(os.environ.get("INQSI_MLB_ML_TARGET_ACCURACY", "90"))
    if len(rows) < min_rows:
        return {
            "ok": False,
            "reason": "not_enough_rows",
            "rowCount": len(rows),
            "minRows": min_rows,
            "validatedAgainstTarget": False,
            "featureVectorVersion": ML_FEATURE_VECTOR_VERSION,
        }
    holdout_count = max(1, int(len(rows) * float(os.environ.get("INQSI_MLB_ML_HOLDOUT_FRAC", "0.25"))))
    train_rows = rows[:-holdout_count]
    holdout = rows[-holdout_count:]
    weights: Dict[str, float] = {}
    means: Dict[str, float] = {}
    scales: Dict[str, float] = {}
    for feature in ML_FEATURES:
        vals = [_f(r.get(feature)) for r in train_rows]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        scale = math.sqrt(var) or 1.0
        pos = [(_f(r.get(feature)) - mean) / scale for r in train_rows if int(r.get("label") or 0) == 1]
        neg = [(_f(r.get(feature)) - mean) / scale for r in train_rows if int(r.get("label") or 0) == 0]
        weights[feature] = round((sum(pos) / len(pos) if pos else 0.0) - (sum(neg) / len(neg) if neg else 0.0), 6)
        means[feature] = mean
        scales[feature] = scale
    bias = math.log((sum(1 for r in train_rows if int(r.get("label") or 0) == 1) + 1) / (sum(1 for r in train_rows if int(r.get("label") or 0) == 0) + 1))

    def predict(r: Dict[str, Any]) -> float:
        z = bias + sum(weights[f] * ((_f(r.get(f)) - means[f]) / (scales[f] or 1.0)) for f in ML_FEATURES)
        return _sigmoid(z)

    scored = [{"p": predict(r), "label": int(r.get("label") or 0), "matchup": r.get("matchup")} for r in holdout]
    candidates = []
    for i in range(50, 96):
        threshold = i / 100.0
        selected = [r for r in scored if r["p"] >= threshold]
        correct = [r for r in selected if r["label"] == 1]
        candidates.append({
            "threshold": threshold,
            "selectedCount": len(selected),
            "correct": len(correct),
            "accuracyPct": round(len(correct) / len(selected) * 100.0, 2) if selected else None,
        })
    viable = [c for c in candidates if c.get("selectedCount") and (c.get("accuracyPct") or 0) >= target]
    non_empty = [c for c in candidates if c.get("selectedCount")]
    selected_threshold = (
        sorted(viable, key=lambda x: (x["selectedCount"], x["accuracyPct"]), reverse=True)[0]
        if viable else
        sorted(non_empty, key=lambda x: ((x.get("accuracyPct") or 0), x["selectedCount"]), reverse=True)[0]
        if non_empty else
        {"threshold": 0.95, "selectedCount": 0, "correct": 0, "accuracyPct": None}
    )
    selected_threshold["validated"] = bool((selected_threshold.get("accuracyPct") or 0) >= target and selected_threshold.get("selectedCount"))
    return {
        "ok": True,
        "version": "MLB-ML-HOLDOUT-SCORER-v2-directional-locked-card",
        "rowCount": len(rows),
        "trainCount": len(train_rows),
        "holdoutCount": len(holdout),
        "features": ML_FEATURES,
        "featureVectorVersion": ML_FEATURE_VECTOR_VERSION,
        "bias": bias,
        "weights": weights,
        "means": means,
        "scales": scales,
        "selectedThreshold": selected_threshold,
        "validatedAgainstTarget": bool(selected_threshold.get("validated")),
        "holdoutThresholdCandidates": candidates,
        "policy": "Use as gated overlay only when holdout threshold is validated against target accuracy.",
    }


def _write_ml_artifacts(report: Dict[str, Any], module: Any) -> Dict[str, Any]:
    current_rows = report.get("rows") or []
    try:
        historical_rows = module.historical_audit_rows()
    except Exception:
        historical_rows = []
    training_rows = _dedupe_training_rows(list(current_rows or []) + list(historical_rows or []))
    records = _dataset(training_rows)
    os.makedirs("runtime_reports", exist_ok=True)
    csv_path = "runtime_reports/mlb_ml_training_dataset_latest.csv"
    model_path = "runtime_reports/mlb_ml_model_latest.json"
    columns = ["sport", "slateDate", "commenceTime", "matchup", "predictedWinner", "winner", *ML_FEATURES, "label"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for rec in records:
            writer.writerow({k: rec.get(k, "") for k in columns})
    model = _train(records)
    model["datasetPath"] = csv_path
    model["modelPath"] = model_path
    model["trainingSource"] = {
        "currentRows": len([r for r in current_rows if r.get("status") == "GRADED"]),
        "historicalRows": len([r for r in historical_rows if r.get("status") == "GRADED"]),
        "dedupedTrainingRows": len(training_rows),
        "recordsWritten": len(records),
        "lockedCardAuditRequired": True,
    }
    with open(model_path, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=2, default=str)
        f.write("\n")
    return {
        "ok": True,
        "recordCount": len(records),
        "datasetPath": csv_path,
        "modelPath": model_path,
        "featureVectorVersion": ML_FEATURE_VECTOR_VERSION,
        "trainingSource": model["trainingSource"],
        "modelSummary": {k: v for k, v in model.items() if k not in {"weights", "means", "scales", "holdoutThresholdCandidates"}},
    }


def _target_rows(actionable: List[Dict[str, Any]], optimized: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
    if actionable:
        return actionable, "explicit_actionable_or_official_accuracy_target_rows"
    targetable_optimized = [row for row in optimized if not _is_no_pick(row)]
    if targetable_optimized:
        return targetable_optimized, "optimized_rows_excluding_pass_no_pick_rows"
    return [], "no_explicit_actionable_or_targetable_optimized_rows"


def apply(module):
    if getattr(module, "_INQSI_MLB_AUDIT_ACTIONABILITY_APPLIED", False):
        return module
    original_build = module.build

    def patched_predictions_index(finals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        dates = sorted(set([f.get("slateDateEt") for f in finals if f.get("slateDateEt")]))
        index: Dict[str, Dict[str, Any]] = {}
        for slate in dates:
            for pred in module._query_predictions_for_slate(slate):
                key = f"{module.normalize_team(pred.get('awayTeam'))}|{module.normalize_team(pred.get('homeTeam'))}"
                if not key.strip("|"):
                    continue
                current = index.get(key)
                if current is None or _prediction_quality(pred) > _prediction_quality(current):
                    index[key] = pred
        return index

    def patched_audit_rows(finals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        index = module.predictions_index(finals)
        rows = []
        for final in finals:
            pred = index.get(final.get("gameKeyBase")) or {}
            if not pred:
                rows.append({**final, "status": "MISSING_PREDICTION"})
                continue
            correct = module.normalize_team(pred.get("predictedWinner")) == module.normalize_team(final.get("winner"))
            rows.append({
                **final,
                "status": "GRADED",
                "predictedWinner": pred.get("predictedWinner"),
                "predictedSide": pred.get("predictedSide"),
                "score": pred.get("score"),
                "winProbabilityPct": pred.get("winProbabilityPct"),
                "confidenceTier": pred.get("confidenceTier"),
                "tags": pred.get("tags") or [],
                "winnerOptimizer": pred.get("winnerOptimizer"),
                "winnerStackV2": pred.get("winnerStackV2"),
                "officialPick": pred.get("officialPick"),
                "officialPrediction": pred.get("officialPrediction"),
                "actionablePick": pred.get("actionablePick"),
                "accuracyTargetEligible": pred.get("accuracyTargetEligible"),
                "actionability": pred.get("actionability"),
                "actionabilityReason": pred.get("actionabilityReason"),
                "actionabilityRiskReasons": pred.get("actionabilityRiskReasons") or [],
                "homeSignal": pred.get("homeSignal"),
                "awaySignal": pred.get("awaySignal"),
                "correct": correct,
            })
        return rows

    def patched_summarize(rows: List[Dict[str, Any]], historical_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        historical_rows = historical_rows or []
        graded = [r for r in rows if r.get("status") == "GRADED"]
        optimized_applied = [r for r in graded if _is_optimized(r)]
        flipped = [r for r in graded if r.get("optimizerFlippedPick")]
        actionable = [r for r in graded if _is_actionable(r)]
        actionable_correct = [r for r in actionable if r.get("correct")]
        target_rows, target_policy = _target_rows(actionable, optimized_applied)
        target_correct = [r for r in target_rows if r.get("correct")]
        all_rows = module._dedupe_rows((rows or []) + (historical_rows or []))
        seven_day_rows = module._rows_since(all_rows, 7)
        thirty_day_rows = module._rows_since(all_rows, 30)
        season_rows = module._rows_since(all_rows, None)
        target_accuracy = _accuracy(target_rows)
        return {
            "windowHours": module.WINDOW_HOURS,
            "targetAccuracyPct": module.TARGET_ACCURACY_PCT,
            "completedFinalGames": len(rows),
            "gradedPredictionCount": len(graded),
            "missingPredictionCount": len(rows) - len(graded),
            "optimizedPickCount": len(target_rows),
            "optimizedCorrect": len(target_correct),
            "optimizedWrong": len(target_rows) - len(target_correct),
            "rolling24hOptimizedAccuracyPct": target_accuracy,
            "rolling24hTargetMet": (target_accuracy >= module.TARGET_ACCURACY_PCT) if target_accuracy is not None else None,
            "winnerOptimizerAppliedCount": len(optimized_applied),
            "winnerOptimizerFlipCount": len(flipped),
            "allScoredPickAccuracyPct": module._accuracy(graded),
            "historicalRowsUsedForLearning": len(module._dedupe_rows(historical_rows)),
            "sevenDayRowsUsedForLearning": len(seven_day_rows),
            "sevenDayAccuracyPct": module._accuracy(seven_day_rows),
            "thirtyDayRowsUsedForLearning": len(thirty_day_rows),
            "thirtyDayAccuracyPct": module._accuracy(thirty_day_rows),
            "seasonRowsUsedForLearning": len(season_rows),
            "seasonAccuracyPct": module._accuracy(season_rows),
            "multiWindowWeights": module.MULTI_WINDOW_WEIGHTS,
            "actionablePickCount": len(actionable),
            "actionableCorrect": len(actionable_correct),
            "actionableWrong": len(actionable) - len(actionable_correct),
            "rolling24hActionableAccuracyPct": _accuracy(actionable),
            "accuracyTargetRowPolicy": target_policy,
            "excludedNoPickOptimizedRows": len([r for r in graded if _is_no_pick(r) and not _is_actionable(r)]),
            "actionabilityPolicy": (
                "Actionable metrics count explicit actionablePick/officialPick/accuracyTargetEligible rows. "
                "Pass/no-pick rows remain graded for learning and all-scored diagnostics, but they are excluded "
                "from optimized accuracy-target proof so the 90% target measures only real display/official picks."
            ),
        }

    def patched_build(*args, **kwargs):
        report = original_build(*args, **kwargs)
        if isinstance(report, dict) and os.environ.get("INQSI_MLB_ML_ARTIFACTS_ENABLED", "true").lower() in {"1", "true", "yes"}:
            try:
                report["mlTraining"] = _write_ml_artifacts(report, module)
                if kwargs.get("store", True):
                    try:
                        report["stored"] = module.store_report(report)
                    except Exception as exc:
                        report["mlTrainingStoreError"] = str(exc)
                if kwargs.get("write_file", True):
                    with open(module.REPORT_PATH, "w", encoding="utf-8") as f:
                        json.dump(report, f, indent=2, default=str)
                        f.write("\n")
            except Exception as exc:
                report["mlTraining"] = {"ok": False, "error": str(exc)}
        return report

    module.predictions_index = patched_predictions_index
    module.audit_rows = patched_audit_rows
    module.summarize = patched_summarize
    module.build = patched_build
    module._INQSI_MLB_AUDIT_ACTIONABILITY_APPLIED = True
    return module
