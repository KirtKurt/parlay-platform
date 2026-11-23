from __future__ import annotations

import math
import os
import threading
import time
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

import boto3

import mlb_ml_deployment_identity_v1 as deployment_identity


VERSION = "MLB-ML-V2-INFERENCE-CONSUMER-v1-gate-promoted-ddb-only"
CHAMPION_PK = "MLB_ML_CHAMPION#V2"
CHAMPION_SK = "ACTIVE"
_CACHE_SECONDS = 60.0
_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {"loadedAt": 0.0, "champion": None, "status": None}
_INSTALLED = False
_ORIGINAL_PREDICT_ALL = None


class V2InferenceConsumerError(RuntimeError):
    pass


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _table():
    name = str(os.environ.get("SNAPSHOTS_TABLE") or "").strip()
    if not name:
        return None
    return boto3.resource("dynamodb").Table(name)


def _payload(item: Any) -> Dict[str, Any]:
    if not isinstance(item, Mapping):
        return {}
    value = _plain(dict(item))
    data = value.get("data")
    return dict(data) if isinstance(data, Mapping) else value


def _promotion_passed(payload: Mapping[str, Any]) -> bool:
    gate = payload.get("promotionGate") if isinstance(payload.get("promotionGate"), Mapping) else {}
    authority = payload.get("authority") if isinstance(payload.get("authority"), Mapping) else {}
    return bool(
        payload.get("promotionPassed") is True
        or gate.get("passed") is True
        or authority.get("promotionPassed") is True
    )


def _direction_authority(payload: Mapping[str, Any]) -> bool:
    gate = payload.get("promotionGate") if isinstance(payload.get("promotionGate"), Mapping) else {}
    authority = payload.get("authority") if isinstance(payload.get("authority"), Mapping) else {}
    return bool(
        payload.get("directionAuthorityEnabled") is True
        or gate.get("directionAuthorityEnabled") is True
        or authority.get("directionAuthorityEnabled") is True
    )


def _live_authority(payload: Mapping[str, Any]) -> bool:
    authority = payload.get("authority") if isinstance(payload.get("authority"), Mapping) else {}
    return bool(
        payload.get("liveInferenceAuthority") is True
        or payload.get("productionAuthorityEnabled") is True
        or authority.get("liveInferenceAuthority") is True
    )


def _model(payload: Mapping[str, Any]) -> Dict[str, Any]:
    candidates: List[Any] = [
        payload.get("outcomeModel"),
        payload.get("directionModel"),
        payload.get("model"),
    ]
    models = payload.get("models")
    if isinstance(models, Mapping):
        candidates.extend([models.get("outcome"), models.get("direction")])
    candidate = payload.get("candidate")
    if isinstance(candidate, Mapping):
        candidates.extend(
            [
                candidate.get("outcomeModel"),
                candidate.get("directionModel"),
                candidate.get("model"),
            ]
        )
    for value in candidates:
        if isinstance(value, Mapping) and value:
            return _plain(dict(value))
    return {}


def champion_errors(payload: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not payload:
        return ["no_active_v2_champion"]
    if not _promotion_passed(payload):
        errors.append("champion_promotion_gate_not_passed")
    if not _direction_authority(payload):
        errors.append("champion_direction_authority_disabled")
    if not _live_authority(payload):
        errors.append("champion_live_inference_authority_disabled")
    identity = payload.get("deploymentIdentity") or payload.get("deployment_identity")
    if not deployment_identity.matches_current(identity):
        errors.append("champion_deployment_identity_mismatch")
    model = _model(payload)
    coefficients = model.get("coefficients") or model.get("weights")
    if not isinstance(coefficients, (Mapping, list, tuple)) or not coefficients:
        errors.append("champion_outcome_model_coefficients_missing")
    if payload.get("immutable") is False:
        errors.append("champion_not_immutable")
    return sorted(set(errors))


def load_active_champion(*, force: bool = False) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and now - float(_CACHE.get("loadedAt") or 0.0) < _CACHE_SECONDS:
            return _CACHE.get("champion"), dict(_CACHE.get("status") or {})

        table = _table()
        payload: Dict[str, Any] = {}
        load_error: Optional[str] = None
        if table is None:
            load_error = "snapshots_table_not_configured"
        else:
            try:
                response = table.get_item(
                    Key={"PK": CHAMPION_PK, "SK": CHAMPION_SK},
                    ConsistentRead=True,
                )
                payload = _payload(response.get("Item"))
            except Exception as exc:  # fail closed; never expose provider details
                load_error = type(exc).__name__

        errors = champion_errors(payload)
        if load_error:
            errors.append(load_error)
        active = not errors
        champion = payload if active else None
        status = {
            "ok": active,
            "version": VERSION,
            "installed": True,
            "championFound": bool(payload),
            "directionAuthorityActive": active,
            "productionAuthoritySource": "gate_promoted_DynamoDB_champion_v2_only",
            "deploymentIdentity": deployment_identity.current_identity(),
            "errors": sorted(set(errors)),
            "fallbackPolicy": "preserve_existing_prediction_when_no_valid_v2_champion",
        }
        _CACHE.update({"loadedAt": now, "champion": champion, "status": status})
        return champion, dict(status)


def _feature_mapping(row: Mapping[str, Any]) -> Dict[str, float]:
    vector = row.get("frozenFeatureVector")
    if not isinstance(vector, Mapping):
        vector = row.get("mlFeatureFreeze")
    features = vector.get("features") if isinstance(vector, Mapping) else None
    if not isinstance(features, Mapping):
        features = row.get("features")
    output: Dict[str, float] = {}
    if isinstance(features, Mapping):
        for key, value in features.items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                output[str(key)] = number
    return output


def _linear_probability(model: Mapping[str, Any], features: Mapping[str, float]) -> float:
    coefficients = model.get("coefficients") or model.get("weights") or {}
    intercept = float(model.get("intercept") or model.get("bias") or 0.0)
    score = intercept
    if isinstance(coefficients, Mapping):
        for name, weight in coefficients.items():
            try:
                score += float(weight) * float(features.get(str(name), 0.0))
            except (TypeError, ValueError):
                continue
    else:
        names = model.get("featureNames") or model.get("features") or []
        for name, weight in zip(names, coefficients):
            try:
                score += float(weight) * float(features.get(str(name), 0.0))
            except (TypeError, ValueError):
                continue
    score = max(min(score, 35.0), -35.0)
    return 1.0 / (1.0 + math.exp(-score))


def _teams(row: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        str(row.get("homeTeam") or row.get("home_team") or "").strip(),
        str(row.get("awayTeam") or row.get("away_team") or "").strip(),
    )


def apply_to_prediction(row: MutableMapping[str, Any], champion: Mapping[str, Any]) -> MutableMapping[str, Any]:
    features = _feature_mapping(row)
    model = _model(champion)
    home, away = _teams(row)
    if not features or not home or not away or not model:
        row["mlbV2InferenceConsumer"] = {
            "applied": False,
            "version": VERSION,
            "reason": "prediction_row_missing_v2_features_or_team_identity",
        }
        return row

    probability = _linear_probability(model, features)
    predicted_side = "home" if probability >= 0.5 else "away"
    predicted_winner = home if predicted_side == "home" else away
    selected_probability = probability if predicted_side == "home" else 1.0 - probability

    original = str(
        row.get("predictedWinner")
        or row.get("predicted_winner")
        or row.get("team")
        or ""
    )
    row["predictedWinner"] = predicted_winner
    row["predicted_winner"] = predicted_winner
    row["predictedSide"] = predicted_side
    row["teamWinProbabilityPct"] = round(selected_probability * 100.0, 6)
    row["mlbV2InferenceConsumer"] = {
        "applied": True,
        "version": VERSION,
        "productionAuthoritySource": "gate_promoted_DynamoDB_champion_v2_only",
        "originalPredictedWinner": original or None,
        "predictedWinner": predicted_winner,
        "predictedSide": predicted_side,
        "homeWinProbability": round(probability, 8),
        "championDigest": champion.get("artifactDigest")
        or champion.get("candidateArtifactDigest")
        or champion.get("digest"),
        "deploymentIdentity": deployment_identity.current_identity(),
    }
    tags = list(row.get("tags") or [])
    if "V2_GATE_PROMOTED_DIRECTION_AUTHORITY" not in tags:
        tags.append("V2_GATE_PROMOTED_DIRECTION_AUTHORITY")
    row["tags"] = tags
    return row


def _prediction_lists(payload: Any) -> Iterable[List[Any]]:
    if not isinstance(payload, Mapping):
        return []
    lists: List[List[Any]] = []
    for key in ("predictions", "gameWinners", "game_winners", "rows", "items", "games"):
        value = payload.get(key)
        if isinstance(value, list):
            lists.append(value)
    return lists


def apply_to_payload(payload: Any) -> Any:
    champion, status = load_active_champion()
    if not isinstance(payload, MutableMapping):
        return payload
    payload["mlbV2InferenceConsumerStatus"] = status
    if champion is None:
        return payload
    applied = 0
    for rows in _prediction_lists(payload):
        for index, row in enumerate(rows):
            if isinstance(row, MutableMapping):
                rows[index] = apply_to_prediction(row, champion)
                applied += int(
                    bool((rows[index].get("mlbV2InferenceConsumer") or {}).get("applied"))
                )
    payload["mlbV2InferenceConsumerStatus"] = {
        **status,
        "predictionRowsApplied": applied,
    }
    return payload


def install(engine_module: Optional[Any] = None) -> Dict[str, Any]:
    global _INSTALLED, _ORIGINAL_PREDICT_ALL
    if _INSTALLED:
        _, status = load_active_champion()
        return {**status, "installed": True, "idempotent": True}
    if engine_module is None:
        try:
            import mlb_game_winner_engine as engine_module  # type: ignore
        except Exception as exc:
            return {
                "ok": False,
                "installed": False,
                "version": VERSION,
                "errors": [f"winner_engine_import_failed:{type(exc).__name__}"],
            }
    predict_all = getattr(engine_module, "predict_all", None)
    if not callable(predict_all):
        return {
            "ok": False,
            "installed": False,
            "version": VERSION,
            "errors": ["winner_engine_predict_all_missing"],
        }

    _ORIGINAL_PREDICT_ALL = predict_all

    def wrapped_predict_all(*args: Any, **kwargs: Any) -> Any:
        return apply_to_payload(_ORIGINAL_PREDICT_ALL(*args, **kwargs))

    setattr(engine_module, "predict_all", wrapped_predict_all)
    _INSTALLED = True
    _, status = load_active_champion(force=True)
    # No champion is a healthy shadow state. Installation—not active authority—
    # is the deployment contract at this stage.
    return {
        **status,
        "ok": True,
        "installed": True,
        "idempotent": False,
        "consumerReadyForFuturePromotedChampion": True,
    }


def status() -> Dict[str, Any]:
    _, current = load_active_champion()
    return {**current, "installed": _INSTALLED}
