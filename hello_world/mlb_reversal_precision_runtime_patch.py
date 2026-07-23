from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List

import mlb_precision_admission_gate_v1 as precision_admission
import mlb_reversal_similarity_v2 as reversal_similarity
import mlb_signal_validation_registry_v1 as validation_registry


VERSION = "MLB-REVERSAL-PRECISION-RUNTIME-v1-70pct-evidence-abstention"


def _selected_signal(row: Dict[str, Any]) -> Dict[str, Any]:
    side = str(row.get("predictedSide") or "").lower()
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _values(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item) for item in values if str(item)]


def _merge_reasons(row: Dict[str, Any], reasons: Iterable[str]) -> List[str]:
    merged = {str(value) for value in reasons if value}
    for field in (
        "blockedReasons",
        "releaseBlockReasons",
        "playabilityBlockReasons",
        "wagerReleaseBlockReasons",
    ):
        merged.update(_values(row.get(field)))
    return sorted(merged)


def enforce_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Fail closed on recommendation labels without changing the visible pick."""
    out = copy.deepcopy(row if isinstance(row, dict) else {})
    selected = _selected_signal(out)
    similarity = reversal_similarity.analyze(selected)
    decision = precision_admission.evaluate(out, selected)
    qualified = decision.get("recommendationEligible") is True

    out["reversalSimilarityV2"] = similarity
    out["precisionAdmission"] = decision
    out["precisionQualifiedRecommendation"] = qualified
    out["futureAccuracyGuaranteed"] = False

    tags = {str(value) for value in (out.get("tags") or []) if value}
    if qualified:
        tags.add("PRECISION_ADMISSION_QUALIFIED")
        out["tags"] = sorted(tags)
        return out

    precision_reasons = [
        f"precision_admission:{reason}"
        for reason in (decision.get("reasons") or ["not_qualified"])
    ]
    merged_reasons = _merge_reasons(out, precision_reasons)
    tags.update(
        {
            "NO_PICK",
            "NOT_PLAYABLE",
            "PRECISION_ADMISSION_NOT_MET",
            "RELEASE_BLOCKED",
            "WAGER_RELEASE_BLOCKED",
        }
    )
    tags.difference_update(
        {"ACTIONABLE_PICK", "PLAYABLE_PREDICTION", "PRECISION_ADMISSION_QUALIFIED"}
    )

    out["playable"] = False
    out["playablePick"] = False
    out["actionablePick"] = False
    out["accuracyTargetEligible"] = False
    out["playableAccuracyEligible"] = False
    out["actionability"] = "NO_PICK"
    out["playabilityStatus"] = "BLOCKED"
    out["recommendationStatus"] = (
        "OFFICIAL_PREDICTION_NOT_PLAYABLE"
        if out.get("officialPrediction") is True
        else "PRE_LOCK_PREDICTION"
    )
    out["blocked"] = True
    out["releaseBlocked"] = True
    out["wagerReleaseBlocked"] = True
    out["actionabilityReason"] = ";".join(precision_reasons)
    for field in (
        "blockedReasons",
        "releaseBlockReasons",
        "playabilityBlockReasons",
        "wagerReleaseBlockReasons",
    ):
        out[field] = merged_reasons

    discipline = dict(out.get("pickDiscipline") or {})
    discipline["precisionAdmissionVersion"] = precision_admission.VERSION
    discipline["precisionAdmissionRequired"] = True
    discipline["precisionQualified"] = False
    discipline["mandatoryBlockReasons"] = sorted(
        set(_values(discipline.get("mandatoryBlockReasons")))
        | {"precision_admission_not_met"}
    )
    discipline["noPickReasons"] = sorted(
        set(_values(discipline.get("noPickReasons")))
        | set(precision_reasons)
    )
    out["pickDiscipline"] = discipline
    out["tags"] = sorted(tags)
    return out


def apply(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_REVERSAL_PRECISION_RUNTIME_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def patched_predict_all(*args: Any, **kwargs: Any) -> Any:
        result = original_predict_all(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        predictions = [enforce_row(row) for row in (result.get("predictions") or [])]
        predictions.sort(
            key=lambda row: (
                float(row.get("actionablePick") is True),
                float(row.get("score") or 0.0),
                float(row.get("winProbability") or 0.0),
            ),
            reverse=True,
        )
        for rank, row in enumerate(predictions, 1):
            row["rank"] = rank
        qualified = sum(row.get("precisionQualifiedRecommendation") is True for row in predictions)
        result["predictions"] = predictions
        result["count"] = len(predictions)
        result["actionablePickCount"] = sum(row.get("actionablePick") is True for row in predictions)
        result["noPickCount"] = len(predictions) - result["actionablePickCount"]
        result["precisionQualifiedRecommendationCount"] = qualified
        result["precisionAbstainedRecommendationCount"] = len(predictions) - qualified

        summary = dict(
            result.get("rolling24hAccuracyTarget")
            or result.get("accuracyTarget")
            or {}
        )
        summary.update(
            {
                "precisionAdmissionEnforced": True,
                "minimumRecommendationEvidencePrecisionPct": (
                    precision_admission.TARGET_PRECISION * 100.0
                ),
                "precisionQualifiedRecommendationCount": qualified,
                "precisionAbstainedRecommendationCount": len(predictions) - qualified,
                "signalValidationRegistry": validation_registry.status(),
                "futureAccuracyGuaranteed": False,
            }
        )
        result["rolling24hAccuracyTarget"] = summary
        result["accuracyTarget"] = summary
        result["precisionAdmissionPolicy"] = {
            "version": VERSION,
            "gateVersion": precision_admission.VERSION,
            "targetPrecisionPct": precision_admission.TARGET_PRECISION * 100.0,
            "visiblePredictionRetained": True,
            "unvalidatedRecommendationAbstained": True,
            "futureAccuracyGuaranteed": False,
        }
        suffix = "+reversal-precision-admission-v1"
        model = str(result.get("modelVersion") or "")
        result["modelVersion"] = model if suffix in model else model + suffix
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_REVERSAL_PRECISION_RUNTIME_APPLIED = True
    module.MLB_REVERSAL_PRECISION_RUNTIME_VERSION = VERSION
    return module
