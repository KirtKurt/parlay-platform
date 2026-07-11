from __future__ import annotations

from typing import Any, Dict, List

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_ml_champion_challenger_v1 as champion_store
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual_model

VERSION = "MLB-ML-CHAMPION-RUNTIME-v1-shadow-until-promotion"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _record(row: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = row.get("frozenFeatureVector") or cohort.freeze_feature_snapshot(row)
    row["frozenFeatureVector"] = snapshot
    row["frozenFeatureVectorVersion"] = snapshot.get("version")
    features = dict(snapshot.get("features") or {})
    features.update({
        "gameId": row.get("gameId") or row.get("id"),
        "commenceTime": row.get("commenceTime"),
        "predictedSide": row.get("predictedSide"),
    })
    return features


def _selected_features(record: Dict[str, Any], side: str) -> Dict[str, Any]:
    home_prob = _f(record.get("homeMarketProb"), 0.5)
    away_prob = _f(record.get("awayMarketProb"), 1.0 - home_prob)
    selected_home = side == "home"
    selected_prob = home_prob if selected_home else away_prob
    opponent_prob = away_prob if selected_home else home_prob
    prefix = "home" if selected_home else "away"
    updated = dict(record)
    updated.update({
        "selectedMarketProb": selected_prob,
        "selectedMarketEdge": selected_prob - opponent_prob,
        "selectedReversalCount": _f(record.get(prefix + "ReversalCount")),
        "selectedBookDivergence": _f(record.get(prefix + "BookDivergence")),
        "selectedDelta": _f(record.get(prefix + "Delta")),
        "selectedFavorite": 1.0 if selected_prob >= 0.5 else 0.0,
        "selectedHome": 1.0 if selected_home else 0.0,
    })
    return updated


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = result.get("predictions") or []
    champion = champion_store.load_champion()
    direction_authority = bool((champion or {}).get("directionAuthorityEnabled"))
    playability_authority = bool((champion or {}).get("playabilityAuthorityEnabled"))
    outcome_model = (champion or {}).get("outcomeModel") or ((champion or {}).get("dualModel") or {}).get("outcomeModel") or {}
    reliability_model = (champion or {}).get("reliabilityModel") or ((champion or {}).get("dualModel") or {}).get("reliabilityModel") or {}
    model_available = bool(outcome_model.get("ok") and reliability_model.get("ok"))
    changed = 0
    playable = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        fundamentals.enhance_row(row)
        record = _record(row)
        outcome_probability = dual_model.score(record, outcome_model) if model_available else None
        original_winner = row.get("predictedWinner")
        original_side = row.get("predictedSide") or "home"
        selected_side = original_side
        if outcome_probability is not None:
            model_side = "home" if outcome_probability >= 0.5 else "away"
            model_winner = row.get("homeTeam") if model_side == "home" else row.get("awayTeam")
            row["outcomeModelHomeWinProbabilityPct"] = round(outcome_probability * 100.0, 2)
            row["outcomeModelAwayWinProbabilityPct"] = round((1.0 - outcome_probability) * 100.0, 2)
            row["outcomeModelPredictedSide"] = model_side
            row["outcomeModelPredictedWinner"] = model_winner
            if direction_authority and model_winner:
                selected_side = model_side
                if model_winner != original_winner:
                    changed += 1
                row["preChampionPredictedWinner"] = original_winner
                row["preChampionPredictedSide"] = original_side
                row["predictedWinner"] = model_winner
                row["predictedSide"] = model_side
                selected_probability = outcome_probability if model_side == "home" else 1.0 - outcome_probability
                row["teamWinProbabilityPct"] = round(selected_probability * 100.0, 2)
                row["winProbabilityPct"] = row["teamWinProbabilityPct"]
                row["winProbabilityMeaning"] = "estimated_probability_selected_team_wins_game"

        reliability_record = _selected_features(record, selected_side)
        reliability_probability = dual_model.score(reliability_record, reliability_model) if model_available else None
        if reliability_probability is not None:
            row["optimizedPickReliabilityPct"] = round(reliability_probability * 100.0, 2)
            row["optimizedPickReliabilityMeaning"] = "champion_probability_selected_pick_is_correct"
        threshold_info = reliability_model.get("selectedThreshold") or {}
        threshold = _f(threshold_info.get("threshold"), 0.70)
        champion_playable = bool(playability_authority and reliability_probability is not None and reliability_probability >= threshold)
        row["championPlayable"] = champion_playable
        row["championReliabilityThreshold"] = threshold
        if playability_authority:
            row["actionablePick"] = champion_playable
            row["playable"] = champion_playable
            row["playablePick"] = champion_playable
            row["accuracyTargetEligible"] = champion_playable
            if champion_playable:
                playable += 1
                tags = set(row.get("tags") or [])
                tags.update({"ML_V3_CHAMPION_PLAYABLE", "ACTIONABLE_PICK"})
                tags.difference_update({"NOT_PLAYABLE", "ML_REJECTED"})
                row["tags"] = sorted(tags)
                row["recommendationStatus"] = "PLAYABLE_PREDICTION"
            else:
                tags = set(row.get("tags") or [])
                tags.update({"ML_V3_CHAMPION_NOT_PLAYABLE", "NOT_PLAYABLE"})
                row["tags"] = sorted(tags)
                row["recommendationStatus"] = "OFFICIAL_PREDICTION_NOT_PLAYABLE"
        else:
            row["mlOptimizationShadowOnly"] = True
        row["mlOptimizationRuntime"] = {
            "applied": True,
            "version": VERSION,
            "championAvailable": bool(champion),
            "modelAvailable": model_available,
            "directionAuthorityEnabled": direction_authority,
            "playabilityAuthorityEnabled": playability_authority,
            "shadowOnly": not (direction_authority or playability_authority),
            "outcomeModelVersion": outcome_model.get("version"),
            "reliabilityModelVersion": reliability_model.get("version"),
        }

    result["mlOptimizationRuntime"] = {
        "applied": True,
        "version": VERSION,
        "championAvailable": bool(champion),
        "modelAvailable": model_available,
        "directionAuthorityEnabled": direction_authority,
        "playabilityAuthorityEnabled": playability_authority,
        "shadowOnly": not (direction_authority or playability_authority),
        "predictionChanges": changed,
        "championPlayableCount": playable,
        "rowCount": len(rows),
        "safetyPolicy": "No challenger may change production direction or playability until a promoted champion exists in DynamoDB.",
    }
    return result


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED", False):
        return module
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = enhance_result(original(*args, **kwargs))
        if kwargs.get("store") and hasattr(module, "_store_prediction"):
            stored = 0
            errors: List[str] = []
            for row in result.get("predictions") or []:
                try:
                    response = module._store_prediction(row)
                    if isinstance(response, dict) and response.get("ok"):
                        stored += 1
                    else:
                        errors.append(str(response))
                except Exception as exc:
                    errors.append(str(exc))
            result["mlOptimizationRuntimeStoredCount"] = stored
            result["mlOptimizationRuntimeStoreErrors"] = errors
        return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_ML_CHAMPION_RUNTIME_V1_APPLIED = True
    return module
