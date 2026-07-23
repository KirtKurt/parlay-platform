#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def query(table: Any, pk: str, prefix: str | None = None) -> list[dict[str, Any]]:
    expression = Key("PK").eq(pk)
    if prefix:
        expression = expression & Key("SK").begins_with(prefix)
    rows: list[dict[str, Any]] = []
    start_key = None
    while True:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": expression,
            "ConsistentRead": True,
            "ScanIndexForward": True,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        response = table.query(**kwargs)
        rows.extend(plain(response.get("Items") or []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return rows


SECRET_RE = re.compile(
    r"(api.?key|secret|password|authorization|credential|access.?key)", re.I
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("<redacted>" if SECRET_RE.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def main() -> int:
    region = os.environ["AWS_REGION"]
    table_name = os.environ.get("SNAPSHOTS_TABLE", "parlay_platform_snapshots")
    slate = os.environ.get("SLATE_DATE", "2026-07-23")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    prediction_items = query(table, f"GAME_WINNERS#mlb#{slate}", "GAME#")
    movement_items = query(table, f"ML_FEATURE#mlb#{slate}")
    predictions = [
        item
        for item in prediction_items
        if item.get("record_type") == "mlb_single_game_moneyline_prediction"
    ]
    report = redact(
        {
            "ok": len(predictions) == 5,
            "readOnly": True,
            "productionWritesPerformed": False,
            "slateDateEt": slate,
            "createdAtUtc": datetime.now(timezone.utc).isoformat(),
            "predictionGameCount": len(predictions),
            "predictionItems": predictions,
            "movementItems": movement_items,
            "secretExposed": False,
        }
    )
    output = Path("/tmp/mlb-signal-breakdown/mlb_signal_breakdown_raw.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "predictionGameCount": report["predictionGameCount"],
                "movementItemCount": len(movement_items),
            },
            indent=2,
        )
    )
    if not report["ok"]:
        raise SystemExit(
            f"Expected five authoritative prediction rows, found {len(predictions)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
