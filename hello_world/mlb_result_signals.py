from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from mlb_audit import _outcomes_for_slate, pull_mlb_results
from mlb_date_signal_api import MLB_PULL_MODE, MLB_PULL_T, movement_deltas

SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")

RESULT_SIGNAL_VERSION = "MLB-RESULT-SIGNAL-LEARNING-v1"
RESULT_SIGNAL_PRODUCER_PROOF_VERSION = "MLB-RESULT-SIGNAL-PRODUCER-PROOF-v1"
RESULT_SIGNAL_PRODUCER_AUTHORITY = "NATIVE_EVENTBRIDGE_SCHEDULE_ENVELOPE"


dynamodb = boto3.resource("dynamodb")
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_ddb_safe(v) for v in value if v is not None]
    return value


def _producer_provenance(value: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("producer_provenance must be an object")
    required = {
        "schema_version",
        "authority",
        "lambda_request_id",
        "event_id",
        "event_time_utc",
        "event_source",
        "detail_type",
        "rule_arn",
        "account",
        "region",
    }
    if set(value) != required:
        raise ValueError(
            "producer_provenance fields mismatch: "
            f"expected={sorted(required)} actual={sorted(value)}"
        )
    normalized = {key: str(value.get(key) or "").strip() for key in required}
    if not all(normalized.values()):
        raise ValueError("producer_provenance contains an empty field")
    if normalized["schema_version"] != RESULT_SIGNAL_PRODUCER_PROOF_VERSION:
        raise ValueError("producer_provenance schema version mismatch")
    if normalized["authority"] != RESULT_SIGNAL_PRODUCER_AUTHORITY:
        raise ValueError("producer_provenance authority mismatch")
    if (
        normalized["event_source"] != "aws.events"
        or normalized["detail_type"] != "Scheduled Event"
    ):
        raise ValueError("producer_provenance EventBridge identity mismatch")
    try:
        uuid.UUID(normalized["lambda_request_id"])
        uuid.UUID(normalized["event_id"])
    except ValueError as exc:
        raise ValueError("producer_provenance request/event ID is invalid") from exc
    try:
        event_time = datetime.fromisoformat(
            normalized["event_time_utc"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("producer_provenance event time is invalid") from exc
    if event_time.tzinfo is None:
        raise ValueError("producer_provenance event time lacks timezone")
    arn = re.fullmatch(
        r"arn:(?:aws|aws-[a-z-]+):events:([a-z0-9-]+):(\d{12}):rule/.+",
        normalized["rule_arn"],
    )
    if (
        arn is None
        or normalized["region"] != arn.group(1)
        or normalized["account"] != arn.group(2)
    ):
        raise ValueError("producer_provenance rule/account/region mismatch")
    normalized["event_time_utc"] = (
        event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    return normalized


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {"statusCode": status, "headers": {"content-type": "application/json", "access-control-allow-origin": "*", "access-control-allow-methods": "GET,POST,OPTIONS"}, "body": json.dumps(body, default=_json_default)}


def _normalize(value: Optional[str]) -> str:
    return " ".join((value or "").lower().strip().split())


def _winner_side(outcome: Dict[str, Any]) -> Optional[str]:
    winner = _normalize(outcome.get("winner"))
    if winner == _normalize(outcome.get("home_team")):
        return "home"
    if winner == _normalize(outcome.get("away_team")):
        return "away"
    return None


def _latest_prediction_by_game(game_date: str) -> Dict[str, Dict[str, Any]]:
    if predictions_tbl is None:
        return {}
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{game_date}"))
    out: Dict[str, Dict[str, Any]] = {}
    for item in resp.get("Items", []) or []:
        game_key = item.get("game_key")
        if not game_key:
            continue
        prior = out.get(game_key)
        if not prior or str(item.get("asof") or item.get("created_at") or "") > str(prior.get("asof") or prior.get("created_at") or ""):
            out[game_key] = item
    return out


def _movement_row_by_game(game_date: str, limit: int = 200) -> Dict[str, Dict[str, Any]]:
    data = movement_deltas(game_date, limit=limit)
    return {row.get("game_key"): row for row in data.get("deltas", []) or [] if row.get("game_key")}


def _hot_side_match(row: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[bool]:
    hot_team = row.get("hot_team") or row.get("attempted_winner") or row.get("predicted_team")
    if not hot_team or not outcome.get("winner"):
        return None
    return _normalize(hot_team) == _normalize(outcome.get("winner"))


def _favorite_match(row: Dict[str, Any], outcome: Dict[str, Any]) -> Optional[bool]:
    favorite = row.get("favorite") or {}
    team = favorite.get("team") or favorite.get("favorite_team")
    if not team or not outcome.get("winner"):
        return None
    return _normalize(team) == _normalize(outcome.get("winner"))


def _book_agreement(row: Dict[str, Any]) -> Dict[str, Any]:
    agreement = row.get("book_agreement") or {}
    return {
        "agreeing_books": agreement.get("agreeing_books"),
        "disagreeing_books": agreement.get("disagreeing_books"),
        "agreement_ratio": agreement.get("agreement_ratio") or agreement.get("book_agreement"),
        "source_status": "CONNECTED" if agreement else "MISSING_IN_SIGNAL_ROW",
    }


def build_result_signal_row(game_date: str, outcome: Dict[str, Any], movement: Dict[str, Any], prediction: Dict[str, Any]) -> Dict[str, Any]:
    game_key = outcome.get("game_key")
    winner_side = _winner_side(outcome)
    hot_match = _hot_side_match(movement or prediction or {}, outcome)
    favorite_match = _favorite_match(movement or prediction or {}, outcome)
    advanced_context = (prediction or {}).get("advanced_context") or (movement or {}).get("advanced_context") or {}
    advanced_blockers = (prediction or {}).get("advanced_blockers") or (movement or {}).get("advanced_blockers") or []
    prediction_status = (prediction or {}).get("prediction_status") or (movement or {}).get("prediction_status") or "NO_STORED_PREDICTION"

    row = {
        "PK": f"RESULT_SIGNAL#mlb#{game_date}",
        "SK": f"GAME#{game_key}",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_ROW",
        "sport": "mlb",
        "game_date_et": game_date,
        "game_key": game_key,
        "version": RESULT_SIGNAL_VERSION,
        "created_at": _now_iso(),
        "learning_status": "SETTLED" if outcome.get("completed") else "PENDING",
        "final_only": True,
        "outcome": {
            "winner_team": outcome.get("winner"),
            "winner_side": winner_side,
            "home_team": outcome.get("home_team"),
            "away_team": outcome.get("away_team"),
            "home_score": outcome.get("home_score"),
            "away_score": outcome.get("away_score"),
            "margin": outcome.get("margin"),
            "total_runs": outcome.get("total_runs"),
            "completed": bool(outcome.get("completed")),
        },
        "prediction": {
            "prediction_status": prediction_status,
            "status": (prediction or {}).get("status"),
            "predicted_team": (prediction or {}).get("predicted_team") or (prediction or {}).get("attempted_winner"),
            "market": (prediction or {}).get("market") or (prediction or {}).get("prediction_market"),
            "success": (prediction or {}).get("success"),
            "confidence_label": (prediction or {}).get("confidence_label"),
        },
        "signals": {
            "pull_mode": MLB_PULL_MODE,
            "snapshot_t_filter": MLB_PULL_T,
            "hot_side": (movement or {}).get("hot_side"),
            "hot_team": (movement or {}).get("hot_team"),
            "hot_delta": (movement or {}).get("hot_delta"),
            "hot_side_matched_winner": hot_match,
            "favorite_matched_winner": favorite_match,
            "home_delta": (movement or {}).get("home_delta"),
            "away_delta": (movement or {}).get("away_delta"),
            "spread_signal": (movement or {}).get("spread_signal"),
            "total_signal": (movement or {}).get("total_signal"),
            "book_agreement": _book_agreement(movement or {}),
            "latest_consensus": (movement or {}).get("latest_consensus"),
            "previous_consensus": (movement or {}).get("previous_consensus"),
            "reason_codes": (movement or {}).get("reason_codes") or (prediction or {}).get("reason_codes") or [],
        },
        "advanced_context": {
            "advanced_eligible": bool((prediction or {}).get("advanced_eligible") or (movement or {}).get("advanced_eligible")),
            "advanced_blockers": advanced_blockers,
            "source_status": advanced_context.get("advanced_eligibility") or "SOURCE_REQUIRED_FOR_ADVANCED_FIELDS",
        },
        "weight_learning": {
            "sample_policy": "Do not change weights from one slate. Accumulate settled rows before adjustment.",
            "minimum_rows_before_adjustment": 30,
            "eligible_for_weight_change_now": False,
        },
    }
    return _ddb_safe(row)


def summarize_result_signals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    settled = [r for r in rows if r.get("learning_status") == "SETTLED"]
    hot_known = [r for r in settled if (r.get("signals") or {}).get("hot_side_matched_winner") is not None]
    hot_hits = [r for r in hot_known if (r.get("signals") or {}).get("hot_side_matched_winner") is True]
    favorite_known = [r for r in settled if (r.get("signals") or {}).get("favorite_matched_winner") is not None]
    favorite_hits = [r for r in favorite_known if (r.get("signals") or {}).get("favorite_matched_winner") is True]
    no_edge = [r for r in settled if (r.get("prediction") or {}).get("prediction_status") in {"NO_EDGE", "NO_STORED_PREDICTION"}]
    return {
        "settled_games": len(settled),
        "hot_side_known": len(hot_known),
        "hot_side_matched_winner": len(hot_hits),
        "hot_side_hit_rate": round(len(hot_hits) / len(hot_known), 4) if hot_known else None,
        "favorite_known": len(favorite_known),
        "favorite_matched_winner": len(favorite_hits),
        "favorite_hit_rate": round(len(favorite_hits) / len(favorite_known), 4) if favorite_known else None,
        "no_edge_or_no_stored_prediction": len(no_edge),
        "weight_change_recommendation": "NO_CHANGE_SAMPLE_TOO_SMALL" if len(settled) < 30 else "REVIEW_REQUIRED",
        "message": "Winner labels have been attached to available market signals. Weight changes require larger settled samples.",
    }


def build_result_signals(
    game_date: str,
    *,
    fetch_scores: bool = True,
    limit: int = 200,
    store: bool = True,
    producer_provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    # Provenance is an authority boundary. Validate it before score fetching,
    # reads, or any per-game/summary mutation can occur.
    provenance = _producer_provenance(producer_provenance)
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    if fetch_scores:
        pull_mlb_results(days_from=3)
    outcomes = _outcomes_for_slate(game_date)
    movement_by_game = _movement_row_by_game(game_date, limit=limit)
    prediction_by_game = _latest_prediction_by_game(game_date)
    rows = []
    stored = 0
    for outcome in outcomes:
        if not outcome.get("completed"):
            continue
        game_key = outcome.get("game_key")
        row = build_result_signal_row(game_date, outcome, movement_by_game.get(game_key, {}), prediction_by_game.get(game_key, {}))
        rows.append(row)
        if store:
            signal_ledger_tbl.put_item(Item=row)
            stored += 1
    summary_created_at = _now_iso()
    summary = _ddb_safe({
        "PK": f"RESULT_SIGNAL#mlb#{game_date}",
        "SK": f"SUMMARY#{summary_created_at}",
        "entity_type": "MLB_RESULT_SIGNAL_LEARNING_SUMMARY",
        "sport": "mlb",
        "game_date_et": game_date,
        "version": RESULT_SIGNAL_VERSION,
        "created_at": summary_created_at,
        "stored_rows": stored,
        "summary": summarize_result_signals(rows),
        **({"producer_provenance": provenance} if provenance is not None else {}),
    })
    if store:
        signal_ledger_tbl.put_item(Item=summary)
        if provenance is not None:
            print(
                json.dumps(
                    {
                        "event": "MLB_RESULT_SIGNAL_SUMMARY_PERSISTED",
                        "schema_version": RESULT_SIGNAL_PRODUCER_PROOF_VERSION,
                        "lambda_request_id": provenance["lambda_request_id"],
                        "event_id": provenance["event_id"],
                        "rule_arn": provenance["rule_arn"],
                        "PK": summary["PK"],
                        "SK": summary["SK"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "version": RESULT_SIGNAL_VERSION,
        "stored_rows": stored,
        "result_signal_rows": rows,
        "summary": summary.get("summary"),
        "summary_key": {"PK": summary["PK"], "SK": summary["SK"]},
        **({"producer_provenance": provenance} if provenance is not None else {}),
    }


def latest_result_signals(game_date: str, limit: int = 200) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"RESULT_SIGNAL#mlb#{game_date}"), ScanIndexForward=False, Limit=limit)
    items = resp.get("Items", [])
    rows = [item for item in items if item.get("entity_type") == "MLB_RESULT_SIGNAL_LEARNING_ROW"]
    summaries = [item for item in items if item.get("entity_type") == "MLB_RESULT_SIGNAL_LEARNING_SUMMARY"]
    return {"ok": True, "sport": "mlb", "game_date_et": game_date, "count": len(items), "row_count": len(rows), "summary_count": len(summaries), "latest_summary": summaries[0] if summaries else summarize_result_signals(rows), "items": items}


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    params = event.get("queryStringParameters") or {}
    game_date = params.get("game_date_et") or params.get("date") or params.get("slate_date_et")
    if not game_date:
        return _resp(400, {"ok": False, "error": "game_date_et or date is required"})
    try:
        if method == "POST" or params.get("build", "false").lower() == "true":
            body = json.loads(event.get("body") or "{}") if isinstance(event.get("body"), str) else {}
            fetch_scores = str(body.get("fetch_scores", params.get("fetch_scores", "true"))).lower() != "false"
            store = str(body.get("store", params.get("store", "true"))).lower() != "false"
            return _resp(200, build_result_signals(game_date, fetch_scores=fetch_scores, store=store))
        return _resp(200, latest_result_signals(game_date))
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "game_date_et": game_date, "error": str(exc)})
