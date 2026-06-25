import json
from typing import Any, Dict

from mlb_audit import final_mlb_scores_report, settlement_proof_report, settle_mlb_slate
from mlb_signal_learning import build_signal_learning_report


def _json_default(value: Any) -> Any:
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
    except Exception:
        pass
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    payload.update(event.get("queryStringParameters") or {})
    payload.update(_parse_body(event))
    for key in ("slate_date_et", "date", "days_from", "daysFrom", "fetch_scores"):
        if key in event and key not in payload:
            payload[key] = event[key]
    return payload


def _bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _settlement_args(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slate_date": payload.get("slate_date_et") or payload.get("date"),
        "days_from": int(payload.get("days_from") or payload.get("daysFrom") or 3),
        "fetch_scores": _bool(payload.get("fetch_scores"), True),
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        payload = _payload(event)
        args = _settlement_args(payload)

        if method in {"GET", "POST"} and path in {"/v1/mlb/scores/final", "/v1/results/mlb/final-scores"}:
            return _resp(200, final_mlb_scores_report(**args))

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/proof", "/v1/mlb/settlement/proof_report"}:
            proof_args = {**args, "fetch_scores": _bool(payload.get("fetch_scores"), False)}
            return _resp(200, settlement_proof_report(**proof_args))

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/settlement", "/v1/mlb/settlement/slate"}:
            return _resp(200, settle_mlb_slate(**args))

        if method in {"GET", "POST"} and path in {"/v1/results/mlb/signal-learning", "/v1/mlb/signal-learning"}:
            learn_args = {**args, "fetch_scores": _bool(payload.get("fetch_scores"), False)}
            return _resp(200, build_signal_learning_report(**learn_args))

        # EventBridge scheduled execution: fetch final scores, settle all completed games,
        # and attach an observe-only signal-learning report. No live games are graded.
        if not method:
            settlement = settle_mlb_slate(**args)
            learning = build_signal_learning_report(
                slate_date=args.get("slate_date"),
                days_from=args.get("days_from", 3),
                fetch_scores=False,
            )
            return _resp(200, {**settlement, "signal_learning": learning})

        return _resp(404, {"ok": False, "sport": "mlb", "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
