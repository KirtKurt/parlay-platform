from __future__ import annotations

from typing import Any, Dict, Iterable, List

from mlb_reversal_shape_v1 import VERSION as SHAPE_VERSION
from mlb_reversal_shape_v1 import analyze


VERSION = "MLB-REVERSAL-SHAPE-RUNTIME-PATCH-v1-fail-closed-validated-evidence"
FEATURE_VECTOR_VERSION = "MLB-ML-FEATURE-VECTOR-v4-reversal-shape"
MIN_ACCURACY_CLAIM_PCT = 70.0
_INSTALLED = False


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None or value == "" else float(value)
    except Exception:
        return default


def _selected_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = str(row.get("predictedSide") or "").lower()
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return signal if isinstance(signal, dict) else {}


def _shape(row: Dict[str, Any]) -> Dict[str, Any]:
    return analyze(_selected_signal(row), row.get("tags") or [])


def _clear_unvalidated_playability(row: Dict[str, Any], reason: str) -> None:
    tags = {str(value).upper() for value in (row.get("tags") or [])}
    tags.difference_update({"ACTIONABLE_PICK", "ML_CONFIRMED", "PLAYABLE_PREDICTION"})
    tags.update({"NOT_PLAYABLE", "LOW_CONFIDENCE_PREDICTION", "UNVALIDATED_ACCURACY_EVIDENCE"})
    row.update(
        {
            "officialPick": False,
            "accuracyTargetEligible": False,
            "actionablePick": False,
            "playable": False,
            "playablePick": False,
            "isOfficialDisplayPick": bool(row.get("predictedWinner")),
            "recommendationStatus": "LOW_CONFIDENCE_PREDICTION_NOT_PLAYABLE",
            "actionability": "UNVALIDATED_ACCURACY_EVIDENCE_NOT_PLAYABLE",
            "actionabilityReason": reason,
            "validatedAccuracyClaimPct": None,
            "minimumAccuracyClaimPct": MIN_ACCURACY_CLAIM_PCT,
        }
    )
    row["actionabilityRiskReasons"] = sorted(
        set((row.get("actionabilityRiskReasons") or []) + [reason])
    )
    row["tags"] = sorted(tags)


def _install_signal_policy(signal_policy: Any) -> None:
    if getattr(signal_policy, "_INQSI_REVERSAL_SHAPE_RUNTIME_PATCHED", False):
        return

    original_reasons = signal_policy._signal_risk_gate_reasons
    original_components = signal_policy._components
    original_apply_row = signal_policy._apply_row
    original_display_card = signal_policy._display_card

    def patched_reasons(row: Dict[str, Any]) -> List[str]:
        return sorted(set((original_reasons(row) or []) + (_shape(row).get("hardRiskReasons") or [])))

    def patched_components(row: Dict[str, Any]) -> List[Dict[str, Any]]:
        components = list(original_components(row) or [])
        seen = {str(item.get("name")) for item in components if isinstance(item, dict)}
        for component in _shape(row).get("scoreComponents") or []:
            if isinstance(component, dict) and str(component.get("name")) not in seen:
                components.append(dict(component))
        return components

    def patched_apply_row(row: Dict[str, Any]) -> Dict[str, Any]:
        result = original_apply_row(row)
        shape = _shape(result)
        result["reversalShapeV1"] = shape
        result["reversalShapeVersion"] = SHAPE_VERSION
        result["reversalSimilaritySignature"] = shape.get("similaritySignature")
        result["reversalPatternTags"] = shape.get("patternTags") or []
        if shape.get("blocked") is True:
            _clear_unvalidated_playability(result, "reversal_shape_instability")
            gate = dict(result.get("signalRiskGate") or {})
            gate.update(
                {
                    "applied": True,
                    "blocked": True,
                    "version": VERSION,
                    "reversalShapeVersion": SHAPE_VERSION,
                    "reversalSimilaritySignature": shape.get("similaritySignature"),
                    "reasons": sorted(
                        set((gate.get("reasons") or []) + (shape.get("hardRiskReasons") or []))
                    ),
                }
            )
            result["signalRiskGate"] = gate
        return result

    def patched_display_card(row: Dict[str, Any]) -> Dict[str, Any]:
        card = original_display_card(row)
        shape = row.get("reversalShapeV1") or _shape(row)
        card.update(
            {
                "reversalSimilaritySignature": shape.get("similaritySignature"),
                "reversalPatternTags": shape.get("patternTags") or [],
                "reversalShapeBlocked": shape.get("blocked") is True,
            }
        )
        return card

    signal_policy._signal_risk_gate_reasons = patched_reasons
    signal_policy._components = patched_components
    signal_policy._apply_row = patched_apply_row
    signal_policy._display_card = patched_display_card
    signal_policy.REVERSAL_SHAPE_RUNTIME_VERSION = VERSION
    signal_policy._INQSI_REVERSAL_SHAPE_RUNTIME_PATCHED = True


def _install_feature_vector(feature_module: Any, overlay_module: Any) -> None:
    if getattr(feature_module, "_INQSI_REVERSAL_SHAPE_FEATURES_PATCHED", False):
        overlay_module.feature_vector = feature_module.feature_vector
        return

    original = feature_module.feature_vector
    new_names = [
        "reversalShapeAvailable",
        "reversalShapeMaximumCount",
        "reversalShapeRepresentativeMovePp",
        "reversalShapeDirectionAgreement",
        "reversalShapeLateVelocityRatio",
        "reversalShapeLateConflict",
        "reversalShapeLateShock",
        "reversalShapeHighDensity",
        "reversalShapeLowEfficiencyChurn",
        "reversalShapeLargeLeg",
        "reversalShapeWeakCoverage",
        "reversalShapePersistentTrend",
        "reversalShapeConfirmedRecovery",
        "reversalShapeIndependentConfirmation",
        "reversalShape60mDensity",
        "reversalShape180mDensity",
        "reversalShapeFullPathEfficiency",
        "reversalShapeFullGrossMovePp",
        "reversalShapeFullMaxSwingPp",
    ]

    def patched(row: Dict[str, Any]) -> Dict[str, float]:
        vector = dict(original(row))
        shape = _shape(row)
        horizons = shape.get("horizons") or {}
        h60 = horizons.get("60m") or {}
        h180 = horizons.get("180m") or {}
        full = horizons.get("full") or {}
        vector.update(
            {
                "reversalShapeAvailable": 1.0 if shape.get("available") is True else 0.0,
                "reversalShapeMaximumCount": _f(shape.get("maximumReversalCount")),
                "reversalShapeRepresentativeMovePp": _f(shape.get("representativeMovePp")),
                "reversalShapeDirectionAgreement": _f((shape.get("direction") or {}).get("directionAgreement")),
                "reversalShapeLateVelocityRatio": _f(shape.get("lateVelocityRatio")),
                "reversalShapeLateConflict": 1.0 if shape.get("lateDirectionConflict") is True else 0.0,
                "reversalShapeLateShock": 1.0 if shape.get("lateOppositeShock") is True else 0.0,
                "reversalShapeHighDensity": 1.0 if shape.get("highReversalDensity") is True else 0.0,
                "reversalShapeLowEfficiencyChurn": 1.0 if shape.get("lowEfficiencyChurn") is True else 0.0,
                "reversalShapeLargeLeg": 1.0 if shape.get("largeReversalLeg") is True else 0.0,
                "reversalShapeWeakCoverage": 1.0 if shape.get("weakCoverageHorizons") else 0.0,
                "reversalShapePersistentTrend": 1.0 if shape.get("persistentTrend") is True else 0.0,
                "reversalShapeConfirmedRecovery": 1.0 if shape.get("stableConfirmedRecovery") is True else 0.0,
                "reversalShapeIndependentConfirmation": 1.0 if shape.get("independentConfirmation") is True else 0.0,
                "reversalShape60mDensity": _f(h60.get("reversalDensityPerHour")),
                "reversalShape180mDensity": _f(h180.get("reversalDensityPerHour")),
                "reversalShapeFullPathEfficiency": _f(full.get("pathEfficiency")),
                "reversalShapeFullGrossMovePp": _f(full.get("grossMovePp")),
                "reversalShapeFullMaxSwingPp": _f(full.get("maxReversalSwingPp")),
            }
        )
        return vector

    feature_module.feature_vector = patched
    feature_module.ML_FEATURES = list(dict.fromkeys(list(feature_module.ML_FEATURES) + new_names))
    feature_module.VERSION = FEATURE_VECTOR_VERSION
    feature_module._INQSI_REVERSAL_SHAPE_FEATURES_PATCHED = True
    overlay_module.feature_vector = patched
    overlay_module.FEATURE_VECTOR_VERSION = FEATURE_VECTOR_VERSION


def _install_overlay(overlay_module: Any) -> None:
    if getattr(overlay_module, "_INQSI_REVERSAL_EVIDENCE_GATE_PATCHED", False):
        return
    original = overlay_module.enhance_result

    def patched(result: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(result, dict):
            for row in result.get("predictions") or []:
                if isinstance(row, dict):
                    _clear_unvalidated_playability(row, "validated_untouched_accuracy_evidence_required")
        output = original(result)
        if not isinstance(output, dict):
            return output
        for row in output.get("predictions") or []:
            if not isinstance(row, dict):
                continue
            overlay = row.get("mlOverlay") or {}
            validated = bool(
                isinstance(overlay, dict)
                and overlay.get("validatedAgainstTarget") is True
                and overlay.get("confirmed") is True
            )
            if not validated:
                _clear_unvalidated_playability(row, "validated_untouched_accuracy_evidence_required")
            row["reversalShapeV1"] = _shape(row)
        summary = dict(output.get("mlOverlay") or {})
        summary.update(
            {
                "reversalShapeRuntimeVersion": VERSION,
                "minimumAccuracyClaimPct": MIN_ACCURACY_CLAIM_PCT,
                "failClosedWithoutValidatedUntouchedEvidence": True,
                "visibleWinnerPredictionPreserved": True,
            }
        )
        output["mlOverlay"] = summary
        return output

    overlay_module.enhance_result = patched
    overlay_module.REVERSAL_SHAPE_RUNTIME_VERSION = VERSION
    overlay_module.MIN_ACCURACY_CLAIM_PCT = MIN_ACCURACY_CLAIM_PCT
    overlay_module._INQSI_REVERSAL_EVIDENCE_GATE_PATCHED = True


def install() -> Dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"ok": True, "version": VERSION, "alreadyInstalled": True}

    applied: List[str] = []
    errors: List[str] = []
    try:
        import mlb_signal_policy_v12 as signal_policy

        _install_signal_policy(signal_policy)
        applied.append("signal_policy_reversal_shape")
    except Exception as exc:
        errors.append(f"signal_policy:{exc}")

    try:
        import mlb_ml_feature_vector as feature_module
        import mlb_ml_runtime_overlay as overlay_module

        _install_feature_vector(feature_module, overlay_module)
        _install_overlay(overlay_module)
        applied.extend(["ml_reversal_shape_features", "validated_evidence_fail_closed_gate"])
    except Exception as exc:
        errors.append(f"ml_runtime:{exc}")

    _INSTALLED = not errors
    return {
        "ok": not errors,
        "version": VERSION,
        "shapeVersion": SHAPE_VERSION,
        "featureVectorVersion": FEATURE_VECTOR_VERSION,
        "minimumAccuracyClaimPct": MIN_ACCURACY_CLAIM_PCT,
        "failClosedWithoutValidatedUntouchedEvidence": True,
        "applied": applied,
        "errors": errors,
    }
