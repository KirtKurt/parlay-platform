from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_ml_champion_challenger_v1 as champion
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual_model
import mlb_ml_manual_promotion_only_patch as manual_promotion

manual_promotion.apply(champion)

VERSION = "MLB-ML-OPTIMIZATION-v3.1-clean-dual-manual-champion-promotion"
REPORT_PATH = "runtime_reports/mlb_ml_optimization_status_latest.json"
CLEAN_PATH = "runtime_reports/mlb_ml_clean_cohort_latest.json"
OUTCOME_PATH = "runtime_reports/mlb_ml_outcome_challenger_latest.json"
RELIABILITY_PATH = "runtime_reports/mlb_ml_reliability_challenger_latest.json"
BUNDLE_PATH = "runtime_reports/mlb_ml_challenger_bundle_latest.json"


def _json_write(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
        handle.write("\n")


def _all_rows(module: Any, report: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = list(report.get("rows") or [])
    try:
        historical = list(module.historical_audit_rows() or [])
    except Exception:
        historical = []
    seen = set()
    output: List[Dict[str, Any]] = []
    for row in current + historical:
        key = "|".join([
            str(row.get("id") or row.get("gameId") or ""),
            str(row.get("commenceTime") or ""),
            str(row.get("predictedWinner") or ""),
        ])
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _playable_evidence(report: Dict[str, Any]) -> int:
    accuracy = report.get("realWorldAccuracy") or {}
    progress = accuracy.get("evidenceProgress") or {}
    summary = report.get("summary") or {}
    return max(
        int(progress.get("playableSettledRecommendations") or 0),
        int(summary.get("seasonPlayablePredictionCount") or 0),
    )


def build(module: Any, report: Dict[str, Any], write_files: bool = True, store: bool = True) -> Dict[str, Any]:
    created = datetime.now(timezone.utc).isoformat()
    rows = _all_rows(module, report)
    for row in rows:
        if isinstance(row, dict) and row.get("status") == "GRADED" and not row.get("fundamentalsSnapshot"):
            row["fundamentalsSnapshot"] = fundamentals.build(row)
    clean = cohort.build(rows)
    trained = dual_model.train(clean.get("cleanRows") or [])
    playable_count = _playable_evidence(report)
    gate = champion.evaluate(trained, int(clean.get("cleanRowCount") or 0), playable_count)

    bundle = {
        "ok": True,
        "version": VERSION,
        "createdAtUtc": created,
        "sport": "mlb",
        "mode": "READY_FOR_MANUAL_REVIEW" if gate.get("promotionEligible") else "SHADOW_CHALLENGER",
        "cleanCohort": {key: value for key, value in clean.items() if key not in {"cleanRows", "quarantinedRows"}},
        "dualModel": trained,
        "promotionGate": gate,
        "outcomeModel": trained.get("outcomeModel"),
        "reliabilityModel": trained.get("reliabilityModel"),
        "directionAuthorityEnabled": False,
        "playabilityAuthorityEnabled": False,
        "automaticPromotionSupported": False,
        "manualPromotionRequired": True,
        "manualPromotionFunction": "promote_reviewed_latest",
        "legacyTrainerAuthorityDisabled": True,
        "legacyTrainingArtifacts": "diagnostic_only_not_authoritative",
        "policy": "Only the exact stored post-fix lock-time feature vector may train the dual challenger. The challenger remains shadow-only until its eligible authority is manually reviewed and promoted from DynamoDB.",
    }

    store_result = champion.store_challenger(bundle) if store else {"ok": True, "stored": False}
    promotion_result = champion.promote_if_allowed(bundle) if store else {"ok": True, "promoted": False, "reason": "store_disabled"}
    bundle["stored"] = store_result
    bundle["promotion"] = promotion_result

    if write_files:
        _json_write(CLEAN_PATH, clean)
        _json_write(OUTCOME_PATH, trained.get("outcomeModel") or {})
        _json_write(RELIABILITY_PATH, trained.get("reliabilityModel") or {})
        _json_write(BUNDLE_PATH, bundle)
        _json_write(REPORT_PATH, {
            "ok": True,
            "version": VERSION,
            "createdAtUtc": created,
            "cleanRowCount": clean.get("cleanRowCount"),
            "quarantinedRowCount": clean.get("quarantinedRowCount"),
            "quarantineReasonCounts": clean.get("quarantineReasonCounts"),
            "trainingStatus": trained.get("status"),
            "recordCount": trained.get("recordCount"),
            "dataQuality": trained.get("dataQuality"),
            "split": trained.get("split"),
            "validation": trained.get("validation"),
            "untouchedTest": trained.get("untouchedTest"),
            "promotionGate": gate,
            "automaticPromotionSupported": False,
            "stored": store_result,
            "promotion": promotion_result,
        })

    report["mlOptimizationV3"] = {
        "applied": True,
        "version": VERSION,
        "mode": bundle["mode"],
        "cleanRowCount": clean.get("cleanRowCount"),
        "quarantinedRowCount": clean.get("quarantinedRowCount"),
        "quarantineReasonCounts": clean.get("quarantineReasonCounts"),
        "trainingStatus": trained.get("status"),
        "recordCount": trained.get("recordCount"),
        "dataQuality": trained.get("dataQuality"),
        "split": trained.get("split"),
        "validation": trained.get("validation"),
        "untouchedTest": trained.get("untouchedTest"),
        "promotionGate": gate,
        "outcomeModelVersion": (trained.get("outcomeModel") or {}).get("version"),
        "reliabilityModelVersion": (trained.get("reliabilityModel") or {}).get("version"),
        "testWasUntouched": trained.get("testWasUntouchedDuringFitAndThresholdSelection"),
        "legacyTrainerAuthorityDisabled": True,
        "automaticPromotionSupported": False,
        "stored": store_result,
        "promotion": promotion_result,
    }
    report["mlTrainingAuthority"] = {
        "authoritative": "mlOptimizationV3_clean_dual_model_only",
        "legacyArtifactRole": "diagnostic_only",
        "automaticWeightMutation": False,
        "automaticChampionPromotion": False,
        "productionAuthoritySource": "reviewed_DynamoDB_champion_bundle_only",
    }
    return report


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ML_OPTIMIZATION_V3_APPLIED", False):
        return module
    original_build = module.build

    def patched_build(*args, **kwargs):
        report = original_build(*args, **kwargs)
        try:
            report = build(
                module,
                report,
                write_files=bool(kwargs.get("write_file", True)),
                store=bool(kwargs.get("store", True)),
            )
            if kwargs.get("store", True):
                try:
                    report["stored"] = module.store_report(report)
                except Exception as exc:
                    report["mlOptimizationStoreError"] = str(exc)
            if kwargs.get("write_file", True):
                with open(module.REPORT_PATH, "w", encoding="utf-8") as handle:
                    json.dump(report, handle, indent=2, default=str)
                    handle.write("\n")
        except Exception as exc:
            report["mlOptimizationV3"] = {"applied": False, "version": VERSION, "error": str(exc)}
        return report

    module.build = patched_build
    module._INQSI_MLB_ML_OPTIMIZATION_V3_APPLIED = True
    return module
