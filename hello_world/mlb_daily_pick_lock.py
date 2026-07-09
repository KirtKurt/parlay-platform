from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

import inqsi_pull_history as history
import mlb_game_winner_engine

EASTERN = ZoneInfo("America/New_York")
MODEL_VERSION = "INQSI-MLB-DAILY-LOCK-v1.0"
LOCK_POLICY = "first_mlb_game_minus_45_minutes"

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
LOCK_MINUTES = int(
    os.environ.get("MLB_DAILY_LOCK_MINUTES_BEFORE_FIRST_GAME")
    or os.environ.get("LOCK_MINUTES_BEFORE_FIRST_GAME")
    or "45"
)
REQUIRE_ALL_GAMES_FOR_LOCK = str(
    os.environ.get("MLB_REQUIRE_ALL_GAMES_FOR_LOCK", "true")
).strip().lower() not in {"0", "false", "no", "off"}

DDB = boto3.resource("dynamodb")
TABLE = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
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
    body = event.get("body") if isinstance(event, dict) else None
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _payload(event: Dict[str, Any]) -> Dict[str, Any]:
    event = event or {}
    out: Dict[str, Any] = {}
    if not event.get("httpMethod") and not event.get("requestContext"):
        out.update({k: v for k, v in event.items() if not k.startswith("aws")})
    params = event.get("queryStringParameters") or {}
    if isinstance(params, dict):
        out.update(params)
    out.update(_parse_body(event))
    return out


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "force"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_et() -> datetime:
    return _now_utc().astimezone(EASTERN)


def _today_et() -> str:
    return _now_et().date().isoformat()


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _game_date_et(game: Dict[str, Any]) -> Optional[str]:
    parsed = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return parsed.astimezone(EASTERN).date().isoformat() if parsed else None


def _lock_pk(slate_date: str) -> str:
    return f"LOCKED_PICKS#mlb#{slate_date}"


def _lock_sk() -> str:
    return f"DAILY_LOCK#TMINUS{LOCK_MINUTES}"


def _get_lock_item(slate_date: str) -> Optional[Dict[str, Any]]:
    if TABLE is None:
        return None
    resp = TABLE.get_item(Key={"PK": _lock_pk(slate_date), "SK": _lock_sk()}, ConsistentRead=True)
    return resp.get("Item")


def _lock_response(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    return {
        "locked": True,
        "slateDateEt": item.get("slate_date"),
        "lockedAt": item.get("locked_at"),
        "lockedAtEt": item.get("locked_at_et"),
        "firstGameStartEt": item.get("first_game_start_et"),
        "lockTimeEt": item.get("lock_time_et"),
        "lockMinutesBeforeFirstGame": item.get("lock_minutes_before_first_game"),
        "source": item.get("source"),
        "predictionCount": item.get("prediction_count"),
        "gameCount": item.get("game_count"),
        "allGamesPredicted": item.get("all_games_predicted"),
        "latestPullAt": item.get("latest_pull_at"),
        "picks": (item.get("data") or {}).get("picks") or [],
        "pk": item.get("PK"),
        "sk": item.get("SK"),
    }


def _pulls_for_date(slate_date: str) -> List[Dict[str, Any]]:
    return history.query_pulls("mlb", slate_date, 500)


def _latest_games_for_date(slate_date: str, pulls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not pulls:
        return []
    latest_pull = pulls[-1]
    return [game for game in latest_pull.get("games") or [] if _game_date_et(game) == slate_date]


def _first_start_et(games: List[Dict[str, Any]]) -> Optional[datetime]:
    starts = []
    for game in games:
        parsed = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
        if parsed:
            starts.append(parsed.astimezone(EASTERN))
    return min(starts) if starts else None


def _american_odds(row: Dict[str, Any]) -> Optional[float]:
    side = row.get("predictedSide")
    signal = row.get("homeSignal") if side == "home" else row.get("awaySignal")
    if isinstance(signal, dict):
        return signal.get("averageAmericanOdds")
    return None


def _compact_pick(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rank": row.get("rank"),
        "gameId": row.get("gameId"),
        "gameKey": row.get("gameKey"),
        "commenceTime": row.get("commenceTime"),
        "homeTeam": row.get("homeTeam"),
        "awayTeam": row.get("awayTeam"),
        "predictedWinner": row.get("predictedWinner"),
        "predictedSide": row.get("predictedSide"),
        "opponent": row.get("opponent"),
        "americanOdds": _american_odds(row),
        "winProbability": row.get("winProbability"),
        "winProbabilityPct": row.get("winProbabilityPct"),
        "score": row.get("score"),
        "confidenceTier": row.get("confidenceTier"),
        "pickQuality": row.get("pickQuality"),
        "pullCountForGame": row.get("pullCountForGame"),
        "tags": row.get("tags") or [],
        "reason": row.get("reason"),
    }


def _sort_picks(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(row: Dict[str, Any]) -> str:
        return str(row.get("commenceTime") or "9999")

    return sorted(picks, key=key)


def _status_payload(slate_date: Optional[str] = None) -> Dict[str, Any]:
    slate = slate_date or _today_et()
    existing = _lock_response(_get_lock_item(slate))
    base: Dict[str, Any] = {
        "ok": True,
        "sport": "mlb",
        "modelVersion": MODEL_VERSION,
        "slateDateEt": slate,
        "lockPolicy": LOCK_POLICY,
        "lockMinutesBeforeFirstGame": LOCK_MINUTES,
        "locked": bool(existing),
        "lock": existing,
    }
    if TABLE is None:
        return {**base, "ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    try:
        pulls = _pulls_for_date(slate)
        games = _latest_games_for_date(slate, pulls)
        first = _first_start_et(games)
        lock_time = first - timedelta(minutes=LOCK_MINUTES) if first else None
        now_et = _now_et()
        return {
            **base,
            "pullCount": len(pulls),
            "gameCount": len(games),
            "latestPullAt": pulls[-1].get("pulled_at") if pulls else None,
            "firstGameStartEt": first.isoformat() if first else None,
            "lockTimeEt": lock_time.isoformat() if lock_time else None,
            "nowEt": now_et.isoformat(),
            "lockDue": bool(lock_time and now_et >= lock_time),
            "minutesUntilLock": round((lock_time - now_et).total_seconds() / 60.0, 2) if lock_time and now_et < lock_time else 0,
        }
    except Exception as exc:
        return {**base, "ok": False, "error": str(exc)}


def run_lock(slate_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    slate = slate_date or _today_et()
    if TABLE is None:
        return {"ok": False, "sport": "mlb", "error": "SNAPSHOTS_TABLE not configured"}

    existing = _lock_response(_get_lock_item(slate))
    if existing:
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": True,
            "alreadyLocked": True,
            "lock": existing,
        }

    pulls = _pulls_for_date(slate)
    if not pulls:
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": False,
            "skipped": True,
            "reason": "NO_STORED_ODDS_API_PULL_HISTORY",
            "message": "Daily lock uses stored Odds API pull history only; it does not call the odds feed directly.",
        }

    games = _latest_games_for_date(slate, pulls)
    first = _first_start_et(games)
    if not games or first is None:
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": False,
            "skipped": True,
            "reason": "NO_MLB_GAMES_FOR_SLATE_DATE",
            "pullCount": len(pulls),
        }

    lock_time = first - timedelta(minutes=LOCK_MINUTES)
    now_et = _now_et()
    if now_et < lock_time and not force:
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": False,
            "skipped": True,
            "reason": "WAITING_FOR_T_MINUS_LOCK_WINDOW",
            "nowEt": now_et.isoformat(),
            "firstGameStartEt": first.isoformat(),
            "lockTimeEt": lock_time.isoformat(),
            "minutesUntilLock": round((lock_time - now_et).total_seconds() / 60.0, 2),
        }

    prediction_payload = mlb_game_winner_engine.predict_all(slate, store=True, limit=500)
    predictions = prediction_payload.get("predictions") or []
    game_count = int(prediction_payload.get("gameCount") or len(games))
    all_games_predicted = bool(prediction_payload.get("allGamesPredicted"))

    if not predictions:
        return {
            "ok": False,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": False,
            "reason": "NO_GAME_WINNER_PREDICTIONS_AVAILABLE",
            "predictionPayload": {k: prediction_payload.get(k) for k in ["ok", "pullCount", "gameCount", "count", "message"]},
        }

    if REQUIRE_ALL_GAMES_FOR_LOCK and len(predictions) < game_count:
        return {
            "ok": False,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": False,
            "reason": "INCOMPLETE_DAILY_CARD_NOT_LOCKED",
            "predictionCount": len(predictions),
            "gameCount": game_count,
            "allGamesPredicted": all_games_predicted,
        }

    picks = _sort_picks([_compact_pick(row) for row in predictions])
    now_utc = _now_utc()
    item = history.ddb_safe({
        "PK": _lock_pk(slate),
        "SK": _lock_sk(),
        "record_type": "mlb_daily_locked_individual_game_picks",
        "sport": "mlb",
        "model_version": MODEL_VERSION,
        "game_winner_model": prediction_payload.get("modelVersion"),
        "slate_date": slate,
        "locked": True,
        "locked_at": now_utc.isoformat(),
        "locked_at_et": now_utc.astimezone(EASTERN).isoformat(),
        "first_game_start_et": first.isoformat(),
        "first_game_start_utc": first.astimezone(timezone.utc).isoformat(),
        "lock_time_et": lock_time.isoformat(),
        "lock_minutes_before_first_game": LOCK_MINUTES,
        "lock_policy": LOCK_POLICY,
        "source": "stored_odds_api_pull_history",
        "latest_pull_at": pulls[-1].get("pulled_at"),
        "pull_count": len(pulls),
        "game_count": game_count,
        "prediction_count": len(picks),
        "all_games_predicted": all_games_predicted,
        "data": {
            "picks": picks,
            "predictionSummary": {
                "engine": prediction_payload.get("engine"),
                "modelVersion": prediction_payload.get("modelVersion"),
                "storedCount": prediction_payload.get("storedCount"),
                "allGamesPredicted": all_games_predicted,
            },
        },
        "created_at": now_utc.isoformat(),
    })

    try:
        TABLE.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
        )
        return {
            "ok": True,
            "sport": "mlb",
            "modelVersion": MODEL_VERSION,
            "slateDateEt": slate,
            "locked": True,
            "alreadyLocked": False,
            "lock": _lock_response(item),
        }
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            existing_after_race = _lock_response(_get_lock_item(slate))
            return {
                "ok": True,
                "sport": "mlb",
                "modelVersion": MODEL_VERSION,
                "slateDateEt": slate,
                "locked": True,
                "alreadyLocked": True,
                "lock": existing_after_race,
            }
        raise


def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or event.get("rawPath") or ""
    payload = _payload(event)
    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    slate_date = payload.get("slate_date") or payload.get("slateDateEt") or payload.get("date")
    try:
        if method == "GET" and path.endswith("/status"):
            return _resp(200, _status_payload(slate_date))
        if method == "GET" and path.endswith("/today"):
            status = _status_payload(slate_date)
            return _resp(200, status)
        if method == "POST" or not method:
            return _resp(200, run_lock(slate_date=slate_date, force=_truthy(payload.get("force"))))
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "modelVersion": MODEL_VERSION, "error": str(exc)})


def lambda_handler(event, context):
    return handle(event, context)
