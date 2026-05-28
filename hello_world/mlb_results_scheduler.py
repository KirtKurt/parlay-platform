import json
from typing import Any, Dict

from mlb_audit import evaluate_mlb_predictions, pull_mlb_results


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    event = event or {}
    try:
        days_from = int(event.get("days_from") or event.get("daysFrom") or 3)
        results = pull_mlb_results(days_from=days_from)
        evaluation = evaluate_mlb_predictions()
        return _resp(200, {"ok": True, "sport": "mlb", "results": results, "evaluation": evaluation})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
