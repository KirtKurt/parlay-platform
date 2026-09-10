from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import ClientError

from features import FEATURE_NAMES, implied_american
from model import initial_state, predict_probability, sgd_step

TABLE_NAME = os.environ["TA_TABLE"]
MIN_SAMPLES = int(os.getenv("TA_MIN_TRAINING_SAMPLES", "200"))
table = boto3.resource("dynamodb").Table(TABLE_NAME)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(status: int, body: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body": json.dumps(body, default=str),
    }


def _tour_key(tour: str) -> str:
    tour = (tour or "atp").lower()
    if tour not in {"atp", "wta"}:
        tour = "atp"
    return f"MODEL#{tour.upper()}"


def _load_state(tour: str) -> Dict[str, Any]:
    key = {"PK": _tour_key(tour), "SK": "STATE"}
    item = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if item:
        return item
    seed = initial_state()
    item = {
        "PK": _tour_key(tour),
        "SK": "STATE",
        "tour": tour.lower(),
        "weights": seed["weights"],
        "bias": seed["bias"],
        "version": seed["version"],
        "training_samples": seed["training_samples"],
        "updated_at": _now(),
        "stack": "tennis-alpha",
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        return item
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return table.get_item(Key=key, ConsistentRead=True)["Item"]


def predict(payload: Mapping[str, Any]) -> Dict[str, Any]:
    tour = str(payload.get("tour") or "atp")
    state = _load_state(tour)
    prob, feat = predict_probability(state, payload["signals"])
    match_id = str(payload["match_id"])
    p1 = implied_american(float(payload["signals"]["player_odds"]))
    p2 = implied_american(float(payload["signals"]["opponent_odds"]))
    market = p1 / (p1 + p2)
    samples = int(state["training_samples"])
    table.put_item(
        Item={
            "PK": f"PRED#{match_id}",
            "SK": _now(),
            "tour": tour,
            "player": str(payload["player"]),
            "opponent": str(payload["opponent"]),
            "probability": Decimal(str(prob)),
            "market_fair_prob": Decimal(str(market)),
            "edge": Decimal(str(prob - market)),
            "model_version": int(state["version"]),
            "features": {k: Decimal(str(v)) for k, v in feat.items()},
            "stack": "tennis-alpha",
        }
    )
    return {
        "stack": "tennis-alpha",
        "match_id": match_id,
        "tour": tour,
        "player": payload["player"],
        "opponent": payload["opponent"],
        "probability": prob,
        "market_fair_prob": market,
        "edge": prob - market,
        "model_version": int(state["version"]),
        "training_samples": samples,
        "eligible": samples >= MIN_SAMPLES,
        "reason": "trained_model" if samples >= MIN_SAMPLES else f"shadow_until_{MIN_SAMPLES}",
    }


def settle(payload: Mapping[str, Any]) -> Dict[str, Any]:
    tour = str(payload.get("tour") or "atp")
    match_id = str(payload["match_id"])
    won = bool(payload["player_won"])
    state = _load_state(tour)
    step = sgd_step(state, payload["signals"], won)
    now = _now()
    settlement = {
        "PK": f"SETTLE#{match_id}",
        "SK": "RECORD",
        "tour": tour,
        "player": str(payload["player"]),
        "opponent": str(payload["opponent"]),
        "event_time": str(payload.get("event_time") or now),
        "label": 1 if won else 0,
        "trained_at": now,
        "stack": "tennis-alpha",
    }
    client = boto3.client("dynamodb")
    serializer = boto3.dynamodb.types.TypeSerializer()

    def ser(d: Mapping[str, Any]) -> Dict[str, Any]:
        return {k: serializer.serialize(v) for k, v in d.items()}

    try:
        client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": TABLE_NAME,
                        "Item": ser(settlement),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Update": {
                        "TableName": TABLE_NAME,
                        "Key": ser({"PK": _tour_key(tour), "SK": "STATE"}),
                        "UpdateExpression": "SET weights=:w, bias=:b, version=:nv, training_samples=:ns, updated_at=:u",
                        "ConditionExpression": "version=:ov",
                        "ExpressionAttributeValues": ser(
                            {
                                ":w": step["weights"],
                                ":b": step["bias"],
                                ":nv": step["version"],
                                ":ns": step["training_samples"],
                                ":u": now,
                                ":ov": int(state["version"]),
                            }
                        ),
                    }
                },
            ]
        )
    except ClientError as exc:
        existing = table.get_item(Key={"PK": f"SETTLE#{match_id}", "SK": "RECORD"}, ConsistentRead=True).get("Item")
        if existing:
            latest = _load_state(tour)
            return {
                "trained": False,
                "duplicate": True,
                "model_version": int(latest["version"]),
                "training_samples": int(latest["training_samples"]),
            }
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise RuntimeError("model state changed; retry") from exc
        raise
    return {
        "trained": True,
        "duplicate": False,
        "pre_update_probability": step["pre_update_probability"],
        "label": 1 if won else 0,
        "model_version": step["version"],
        "training_samples": step["training_samples"],
        "stack": "tennis-alpha",
    }


def status() -> Dict[str, Any]:
    atp = _load_state("atp")
    wta = _load_state("wta")
    return {
        "stack": "tennis-alpha",
        "isolated": True,
        "touches_tennis_learning": False,
        "features": list(FEATURE_NAMES),
        "min_training_samples": MIN_SAMPLES,
        "atp": {
            "model_version": int(atp["version"]),
            "training_samples": int(atp["training_samples"]),
            "eligible": int(atp["training_samples"]) >= MIN_SAMPLES,
            "updated_at": atp.get("updated_at"),
        },
        "wta": {
            "model_version": int(wta["version"]),
            "training_samples": int(wta["training_samples"]),
            "eligible": int(wta["training_samples"]) >= MIN_SAMPLES,
            "updated_at": wta.get("updated_at"),
        },
    }


def lambda_handler(event: Mapping[str, Any], context: Any) -> Dict[str, Any]:
    try:
        method = str(event.get("httpMethod", "GET")).upper()
        path = str(event.get("path", "/"))
        body = json.loads(event.get("body") or "{}")
        if method == "OPTIONS":
            return _response(204, {})
        if method == "GET" and path.endswith("/health"):
            return _response(200, status())
        if method == "GET" and path.endswith("/status"):
            return _response(200, status())
        if method == "POST" and path.endswith("/predict"):
            return _response(200, predict(body))
        if method == "POST" and path.endswith("/settle"):
            return _response(200, settle(body))
        return _response(404, {"error": "not_found", "path": path, "stack": "tennis-alpha"})
    except (KeyError, TypeError, ValueError) as exc:
        return _response(400, {"error": "invalid_request", "detail": str(exc)})
    except RuntimeError as exc:
        return _response(409, {"error": "retryable_conflict", "detail": str(exc)})
    except Exception as exc:
        return _response(500, {"error": "internal_error", "detail": str(exc)})
