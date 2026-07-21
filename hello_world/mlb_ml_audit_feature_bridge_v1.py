from __future__ import annotations

from typing import Any, Dict

VERSION = "MLB-ML-AUDIT-FEATURE-BRIDGE-v1-frozen-lock-vector"

COPY_FIELDS = [
    "slateCoverage",
    "predictionSemanticsVersion",
    "probabilitySemanticsFixed",
    "teamWinProbabilityPct",
    "winProbabilityMeaning",
    "fundamentalsSnapshot",
    "fundamentalsSnapshotVersion",
    "fundamentalsSnapshotV2",
    "fundamentalsSnapshotV2Ref",
    "fundamentalsSnapshotRefV2",
    "probabilityContractVersion",
    "homeModelWinProbability",
    "awayModelWinProbability",
    "modelWinProbability",
    "modelProbabilityVersion",
    "modelProbabilitySource",
    "homeMarketDeVigProbability",
    "awayMarketDeVigProbability",
    "marketProbability",
    "marketProbabilitySourceAtUtc",
    "marketProbabilityVersion",
    "marketProbabilityFingerprint",
    "signalScore",
    "pickReliability",
    "probabilityContract",
    "probabilityCorrectionApplied",
    "probabilityCorrectionReason",
    "pullHistoryIntegrity",
    "predictionSourceCanonicalSlot",
    "frozenFeatureVector",
    "frozenFeatureVectorVersion",
    "frozenOutcomeFeatures",
    "frozenReliabilityFeatures",
    "mlFeatureFreeze",
    "mlOptimizationRuntime",
    "outcomeModelHomeWinProbabilityPct",
    "outcomeModelAwayWinProbabilityPct",
    "optimizedPickReliabilityPct",
]


def apply(base_module: Any):
    if getattr(base_module, "_INQSI_MLB_ML_AUDIT_FEATURE_BRIDGE_APPLIED", False):
        return base_module
    original = base_module._copy_audit_fields

    def copied(pred: Dict[str, Any]) -> Dict[str, Any]:
        out = original(pred)
        for field in COPY_FIELDS:
            if field in pred:
                out[field] = pred.get(field)
        out["mlAuditFeatureBridge"] = {
            "applied": True,
            "version": VERSION,
            "immutableFeatureVectorCopied": bool(pred.get("mlFeatureFreeze") or pred.get("frozenFeatureVector")),
        }
        return out

    base_module._copy_audit_fields = copied
    base_module._INQSI_MLB_ML_AUDIT_FEATURE_BRIDGE_APPLIED = True
    return base_module
