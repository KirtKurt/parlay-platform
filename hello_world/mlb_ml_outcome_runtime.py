from __future__ import annotations

import json
import math
import os
from typing import Any, Dict

from mlb_ml_frozen_features import OUTCOME_FEATURES, VERSION as FREEZE_VERSION, build_outcome_features

VERSION = "MLB-OUTCOME-RUNTIME-v1-approved-clean-champion-only"
MODEL_PATH = os.environ.get("INQSI_MLB_OUTCOME_MODEL_PATH", "runtime_reports/mlb_ml_outcome_champion.json")
VALIDATION_PROTOCOL = "chronological_train_validation_test_v1"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value in (None, "") else float(value)
    except Exception:
        return default


def _load_model() -> Dict[str, Any] | None:
    try:
        with open(MODEL_PATH, encoding="utf-8") as fh:
            model = json.load(fh)
    except Exception:
        return None
    if not isinstance(model, dict) or model.get("ok") is not True:
        return None
    if not (
        model.get("productionApproved") is True
        and model.get("cleanCohort") is True
        and model.get("modelRole") == "outcome"
        and model.get("validationProtocol") == VALIDATION_PROTOCOL
        and model.get("featureFreezeVersion") == FREEZE_VERSION
        and int((model.get("testMetrics") or {}).get("testCount") or 0) >= int(os.environ.get("INQSI_MLB_OUTCOME_MIN_PRODUCTION_TEST_ROWS", "100"))
    ):
        return None
    return model


def _score(features: Dict[str, Any], model: Dict[str, Any]) -> float:
    z = _f(model.get("bias"))
    weights = model.get("weights") or {}
    means = model.get("means") or {}
    scales = model.get("scales") or {}
    for name in model.get("features") or OUTCOME_FEATURES:
        scale = _f(scales.get(name), 1.0) or 1.0
        z += _f(weights.get(name)) * ((_f(features.get(name)) - _f(means.get(name))) / scale)
    return 1.0 if z >= 35 else 0.0 if z <= -35 else 1.0 / (1.0 + math.exp(-z))


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    model = _load_model()
    for row in result.get("predictions") or []:
        if not isinstance(row, dict):
            continue
        features = build_outcome_features(row)
        overlay = {
            "applied": bool(model),
            "runtimeVersion": VERSION,
            "modelAvailable": bool(model),
            "modelVersion": (model or {}).get("version"),
            "productionApproved": bool((model or {}).get("productionApproved")),
            "features": features,
        }
        if model:
            home_probability = _score(features, model)
            selected_side = "home" if home_probability >= 0.5 else "away"
            selected_probability = home_probability if selected_side == "home" else 1.0 - home_probability
            row["predictedSide"] = selected_side
            row["predictedWinner"] = row.get("homeTeam") if selected_side == "home" else row.get("awayTeam")
            row["opponent"] = row.get("awayTeam") if selected_side == "home" else row.get("homeTeam")
            row["teamWinProbabilityPct"] = round(selected_probability * 100.0, 2)
            row["winProbabilityPct"] = row["teamWinProbabilityPct"]
            row["winProbabilityMeaning"] = "approved_outcome_model_probability_selected_team_wins"
            row["outcomeModelSelectedSide"] = selected_side
            tags = set(row.get("tags") or [])
            tags.add("APPROVED_OUTCOME_MODEL")
            row["tags"] = sorted(tags)
            overlay.update({
                "homeWinProbability": round(home_probability, 6),
                "selectedSide": selected_side,
                "selectedTeamWinProbability": round(selected_probability, 6),
            })
        row["mlOutcome"] = overlay
    result["mlOutcome"] = {
        "applied": bool(model),
        "runtimeVersion": VERSION,
        "modelVersion": (model or {}).get("version"),
        "productionApproved": bool((model or {}).get("productionApproved")),
        "rowCount": len(result.get("predictions") or []),
    }
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_OUTCOME_RUNTIME_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        return enhance_result(original(*args, **kwargs))

    module.predict_all = patched_predict_all
    module._INQSI_MLB_OUTCOME_RUNTIME_APPLIED = True
    return module
