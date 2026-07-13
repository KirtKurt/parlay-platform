from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import mlb_fundamentals_snapshot_v1 as fundamentals
import mlb_ml_champion_challenger_v1 as champion_store
import mlb_ml_clean_cohort_v1 as cohort
import mlb_ml_dual_model_v1 as dual_model

VERSION = "MLB-ML-CHAMPION-RUNTIME-v1.1-independent-authority-reselection-safe"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _optional_f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _signal(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    value = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    return value if isinstance(value, dict) else {}


def _side_score(row: Dict[str, Any], side: str) -> Optional[float]:
    return _optional_f(_signal(row, side).get("score"))


def _side_price(row: Dict[str, Any], side: str) -> tuple[Optional[float], Optional[str], Optional[str]]:
    signal = _signal(row, side)
    price = _optional_f(signal.get("americanOdds"))
    if price is None:
        price = _optional_f(signal.get("averageAmericanOdds"))
    book = signal.get("priceBook")
    source = signal.get("priceSource")
    return price, str(book) if book else None, str(source) if source else None


def _validated_reliability_threshold(model: Dict[str, Any]) -> tuple[bool, Optional[float]]:
    threshold_info = model.get("selectedThreshold") or {}
    threshold = _optional_f(threshold_info.get("threshold"))
    valid = bool(
        model.get("ok") is True
        and model.get("thresholdSelectedOnValidationOnly") is True
        and threshold_info.get("ok") is True
        and threshold_info.get("selectionSource") == "validation_only"
        and threshold is not None
        and 0.0 < threshold < 1.0
    )
    return valid, threshold if valid else None


def _record(row: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = row.get("frozenFeatureVector") or cohort.freeze_feature_snapshot(row)
    row["frozenFeatureVector"] = snapshot
    row["frozenFeatureVectorVersion"] = snapshot.get("version")
    features = dict(snapshot.get("features") or {})
    original_side = str(row.get("predictedSide") or "home").lower()
    original_selected_score = _optional_f(features.get("selectedScore"))
    home_score = _side_score(row, "home")
    away_score = _side_score(row, "away")
    if home_score is None and original_side == "home":
        home_score = original_selected_score
    if away_score is None and original_side == "away":
        away_score = original_selected_score
    features.update({
        "gameId": row.get("gameId") or row.get("id"),
        "commenceTime": row.get("commenceTime"),
        "predictedSide": row.get("predictedSide"),
        # Runtime-only side scores let the reliability model follow a champion
        # direction flip without reusing the former selected side's score.
        "homeScore": home_score,
        "awayScore": away_score,
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
        "selectedScore": _f(record.get(prefix + "Score")),
        "selectedFavorite": 1.0 if selected_prob >= 0.5 else 0.0,
        "selectedHome": 1.0 if selected_home else 0.0,
    })
    return updated


def enhance_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    rows = result.get("predictions") or []
    champion = champion_store.load_champion()
    direction_authority_requested = bool((champion or {}).get("directionAuthorityEnabled"))
    playability_authority_requested = bool((champion or {}).get("playabilityAuthorityEnabled"))
    outcome_model = (champion or {}).get("outcomeModel") or ((champion or {}).get("dualModel") or {}).get("outcomeModel") or {}
    reliability_model = (champion or {}).get("reliabilityModel") or ((champion or {}).get("dualModel") or {}).get("reliabilityModel") or {}
    outcome_model_available = bool(outcome_model.get("ok") is True)
    reliability_model_available = bool(reliability_model.get("ok") is True)
    reliability_threshold_valid, reliability_threshold = _validated_reliability_threshold(reliability_model)
    direction_authority = bool(direction_authority_requested and outcome_model_available)
    playability_authority = bool(
        playability_authority_requested
        and reliability_model_available
        and reliability_threshold_valid
    )
    authority_safety_errors: List[str] = []
    if direction_authority_requested and not outcome_model_available:
        authority_safety_errors.append("direction_authority_requested_without_valid_outcome_model")
    if playability_authority_requested and not reliability_model_available:
        authority_safety_errors.append("playability_authority_requested_without_valid_reliability_model")
    if playability_authority_requested and not reliability_threshold_valid:
        authority_safety_errors.append("playability_authority_requested_without_validation_selected_threshold")
    changed = 0
    playable = 0
    direction_flip_playability_blocks = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        fundamentals.enhance_row(row)
        record = _record(row)
        outcome_probability = dual_model.score(record, outcome_model) if outcome_model_available else None
        original_winner = row.get("predictedWinner")
        original_side = str(row.get("predictedSide") or "home").lower()
        if original_side not in {"home", "away"}:
            original_side = "home"
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
                # A direction flip must not leave either the displayed score or
                # the reliability feature tied to the old selected side.
                row["score"] = _optional_f(record.get(model_side + "Score"))
                selected_price, selected_book, selected_price_source = _side_price(row, model_side)
                row["americanOdds"] = selected_price
                if "lockedAmericanOdds" in row:
                    row["lockedAmericanOdds"] = selected_price
                row["priceBook"] = selected_book
                row["priceSource"] = selected_price_source
                selected_probability = outcome_probability if model_side == "home" else 1.0 - outcome_probability
                row["teamWinProbabilityPct"] = round(selected_probability * 100.0, 2)
                row["winProbabilityPct"] = row["teamWinProbabilityPct"]
                row["winProbabilityMeaning"] = "estimated_probability_selected_team_wins_game"

        reliability_record = _selected_features(record, selected_side)
        reliability_probability = dual_model.score(reliability_record, reliability_model) if reliability_model_available else None
        if reliability_probability is not None:
            row["optimizedPickReliabilityPct"] = round(reliability_probability * 100.0, 2)
            row["optimizedPickReliabilityMeaning"] = "champion_probability_selected_pick_is_correct"
        direction_flipped = bool(direction_authority and selected_side != original_side)
        row_playability_safety_reasons: List[str] = []
        if direction_flipped:
            direction_flip_playability_blocks += 1
            row_playability_safety_reasons.append(
                "direction_flip_not_validated_for_reliability_or_selected_side_priced_roi"
            )
        champion_playable = bool(
            playability_authority
            and not direction_flipped
            and reliability_probability is not None
            and reliability_threshold is not None
            and reliability_probability >= reliability_threshold
        )
        row["championPlayable"] = champion_playable
        row["championReliabilityThreshold"] = reliability_threshold
        row["championPlayabilitySafetyReasons"] = row_playability_safety_reasons
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
            "modelAvailable": bool(outcome_model_available and reliability_model_available),
            "outcomeModelAvailable": outcome_model_available,
            "reliabilityModelAvailable": reliability_model_available,
            "reliabilityThresholdValidated": reliability_threshold_valid,
            "directionAuthorityRequested": direction_authority_requested,
            "playabilityAuthorityRequested": playability_authority_requested,
            "directionAuthorityEnabled": direction_authority,
            "playabilityAuthorityEnabled": playability_authority,
            "authoritySafetyErrors": authority_safety_errors,
            "directionFlipped": direction_flipped,
            "playabilitySafetyReasons": row_playability_safety_reasons,
            "shadowOnly": not (direction_authority or playability_authority),
            "outcomeModelVersion": outcome_model.get("version"),
            "reliabilityModelVersion": reliability_model.get("version"),
        }

    result["mlOptimizationRuntime"] = {
        "applied": True,
        "version": VERSION,
        "championAvailable": bool(champion),
        "modelAvailable": bool(outcome_model_available and reliability_model_available),
        "outcomeModelAvailable": outcome_model_available,
        "reliabilityModelAvailable": reliability_model_available,
        "reliabilityThresholdValidated": reliability_threshold_valid,
        "directionAuthorityRequested": direction_authority_requested,
        "playabilityAuthorityRequested": playability_authority_requested,
        "directionAuthorityEnabled": direction_authority,
        "playabilityAuthorityEnabled": playability_authority,
        "authoritySafetyErrors": authority_safety_errors,
        "shadowOnly": not (direction_authority or playability_authority),
        "predictionChanges": changed,
        "championPlayableCount": playable,
        "directionFlipPlayabilityBlockedCount": direction_flip_playability_blocks,
        "rowCount": len(rows),
        "safetyPolicy": "Each authority requires its own promoted valid model; playability also requires a validation-selected reliability threshold.",
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
