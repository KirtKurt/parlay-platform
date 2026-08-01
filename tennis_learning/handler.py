from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Tuple

import boto3
from botocore.exceptions import ClientError

FEATURES: Tuple[str, ...] = (
    "market_fair_prob", "elo_diff_scaled", "surface_elo_diff_scaled",
    "recent_win_rate_diff", "serve_points_won_diff", "return_points_won_diff",
    "break_points_saved_diff", "rest_advantage_scaled", "best_of_five",
)
TABLE_NAME = os.environ["TENNIS_LEARNING_TABLE"]
MIN_SAMPLES = int(os.getenv("TENNIS_MIN_TRAINING_SAMPLES", "100"))
LEARNING_RATE = Decimal(os.getenv("TENNIS_LEARNING_RATE", "0.03"))
L2 = Decimal(os.getenv("TENNIS_L2", "0.0005"))
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(status: int, body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*"}, "body": json.dumps(body, default=str)}


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _implied(odds: float) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    return abs(odds) / (abs(odds) + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def _features(signals: Mapping[str, Any]) -> Dict[str, float]:
    po, oo = float(signals["player_odds"]), float(signals["opponent_odds"])
    p1, p2 = _implied(po), _implied(oo)
    return {
        "market_fair_prob": p1 / (p1 + p2),
        "elo_diff_scaled": max(-3.0, min(3.0, float(signals.get("elo_diff", 0)) / 400.0)),
        "surface_elo_diff_scaled": max(-3.0, min(3.0, float(signals.get("surface_elo_diff", 0)) / 400.0)),
        "recent_win_rate_diff": max(-1.0, min(1.0, float(signals.get("recent_win_rate_diff", 0)))),
        "serve_points_won_diff": max(-1.0, min(1.0, float(signals.get("serve_points_won_diff", 0)))),
        "return_points_won_diff": max(-1.0, min(1.0, float(signals.get("return_points_won_diff", 0)))),
        "break_points_saved_diff": max(-1.0, min(1.0, float(signals.get("break_points_saved_diff", 0)))),
        "rest_advantage_scaled": max(-2.0, min(2.0, float(signals.get("rest_days_diff", 0)) / 7.0)),
        "best_of_five": 1.0 if bool(signals.get("best_of_five", False)) else 0.0,
    }


def _initial_state() -> Dict[str, Any]:
    return {"PK": "MODEL", "SK": "STATE", "weights": [Decimal("2.0")] + [Decimal("0")] * (len(FEATURES)-1), "bias": Decimal("-1.0"), "version": 1, "training_samples": 0, "updated_at": _now()}


def _state() -> Dict[str, Any]:
    item = table.get_item(Key={"PK": "MODEL", "SK": "STATE"}, ConsistentRead=True).get("Item")
    if item:
        return item
    initial = _initial_state()
    try:
        table.put_item(Item=initial, ConditionExpression="attribute_not_exists(PK)")
        return initial
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return table.get_item(Key={"PK": "MODEL", "SK": "STATE"}, ConsistentRead=True)["Item"]


def _vector(features: Mapping[str, float]) -> List[float]:
    return [float(features[name]) for name in FEATURES]


def _predict_probability(state: Mapping[str, Any], features: Mapping[str, float]) -> float:
    return _sigmoid(float(state["bias"]) + sum(float(w) * x for w, x in zip(state["weights"], _vector(features))))


def predict(payload: Mapping[str, Any]) -> Dict[str, Any]:
    f = _features(payload["signals"])
    state = _state()
    probability = _predict_probability(state, f)
    match_id = str(payload["match_id"])
    table.put_item(Item={"PK": f"PREDICTION#{match_id}", "SK": _now(), "player": str(payload["player"]), "opponent": str(payload["opponent"]), "probability": Decimal(str(probability)), "model_version": int(state["version"]), "features": {k: Decimal(str(v)) for k, v in f.items()}})
    samples = int(state["training_samples"])
    return {"match_id": match_id, "player": payload["player"], "opponent": payload["opponent"], "probability": probability, "model_version": int(state["version"]), "training_samples": samples, "eligible": samples >= MIN_SAMPLES, "reason": "trained_model" if samples >= MIN_SAMPLES else f"shadow_only_until_{MIN_SAMPLES}_settlements"}


def _serialize(value: Mapping[str, Any]) -> Dict[str, Any]:
    serializer = boto3.dynamodb.types.TypeSerializer()
    return {k: serializer.serialize(v) for k, v in value.items()}


def settle(payload: Mapping[str, Any]) -> Dict[str, Any]:
    match_id = str(payload["match_id"])
    f = _features(payload["signals"])
    label = 1 if bool(payload["player_won"]) else 0
    state = _state()
    probability = _predict_probability(state, f)
    error = Decimal(str(label - probability))
    vector = [Decimal(str(v)) for v in _vector(f)]
    old_weights = [Decimal(str(v)) for v in state["weights"]]
    new_weights = [w + LEARNING_RATE * (error * x - L2 * w) for w, x in zip(old_weights, vector)]
    new_bias = Decimal(str(state["bias"])) + LEARNING_RATE * error
    now = _now()
    settlement = {"PK": f"SETTLEMENT#{match_id}", "SK": "RECORD", "player": str(payload["player"]), "opponent": str(payload["opponent"]), "event_time": str(payload["event_time"]), "features": {k: Decimal(str(v)) for k, v in f.items()}, "label": label, "trained_at": now}
    client = boto3.client("dynamodb")
    try:
        client.transact_write_items(TransactItems=[
            {"Put": {"TableName": TABLE_NAME, "Item": _serialize(settlement), "ConditionExpression": "attribute_not_exists(PK)"}},
            {"Update": {"TableName": TABLE_NAME, "Key": _serialize({"PK": "MODEL", "SK": "STATE"}), "UpdateExpression": "SET weights=:w, bias=:b, version=:nv, training_samples=:ns, updated_at=:u", "ConditionExpression": "version=:ov", "ExpressionAttributeValues": _serialize({":w": new_weights, ":b": new_bias, ":nv": int(state["version"])+1, ":ns": int(state["training_samples"])+1, ":u": now, ":ov": int(state["version"])})}}
        ])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        existing = table.get_item(Key={"PK": f"SETTLEMENT#{match_id}", "SK": "RECORD"}, ConsistentRead=True).get("Item")
        if existing:
            latest = _state()
            return {"trained": False, "duplicate": True, "model_version": int(latest["version"]), "training_samples": int(latest["training_samples"])}
        if code == "TransactionCanceledException":
            raise RuntimeError("model state changed concurrently; retry settlement") from exc
        raise
    return {"trained": True, "duplicate": False, "pre_update_probability": probability, "label": label, "model_version": int(state["version"])+1, "training_samples": int(state["training_samples"])+1}


def status() -> Dict[str, Any]:
    state = _state()
    samples = int(state["training_samples"])
    return {"service": "tennis-learning", "status": "learning" if samples else "ready_for_settlements", "model_version": int(state["version"]), "training_samples": samples, "minimum_training_samples": MIN_SAMPLES, "eligible": samples >= MIN_SAMPLES, "features": list(FEATURES), "updated_at": state["updated_at"], "persistence": "dynamodb"}


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    try:
        method = str(event.get("httpMethod", "GET")).upper()
        path = str(event.get("path", "/"))
        body = json.loads(event.get("body") or "{}")
        if method == "OPTIONS":
            return _response(204, {})
        if method == "GET" and path.endswith("/health"):
            return _response(200, status())
        if method == "GET" and path.endswith("/model/status"):
            return _response(200, status())
        if method == "POST" and path.endswith("/predict"):
            return _response(200, predict(body))
        if method == "POST" and path.endswith("/settle"):
            return _response(200, settle(body))
        if method == "POST" and path.endswith("/train/batch"):
            counts = {"trained": 0, "duplicates": 0, "rejected": 0}
            for row in body if isinstance(body, list) else []:
                try:
                    result = settle(row)
                    counts["trained"] += int(result["trained"])
                    counts["duplicates"] += int(result["duplicate"])
                except (KeyError, TypeError, ValueError):
                    counts["rejected"] += 1
            return _response(200, counts)
        return _response(404, {"error": "not_found", "path": path})
    except (KeyError, TypeError, ValueError) as exc:
        return _response(400, {"error": "invalid_request", "detail": str(exc)})
    except RuntimeError as exc:
        return _response(409, {"error": "retryable_conflict", "detail": str(exc)})
    except Exception as exc:
        return _response(500, {"error": "internal_error", "detail": str(exc)})
