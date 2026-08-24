from __future__ import annotations

"""Five-minute autonomous controller for the official MLB daily card.

It never rewrites a locked prediction. It keeps the market/context pull fresh,
requests training on a bounded cadence, repeatedly asks the existing canonical
lock authority to build the full official slate, and verifies that the first
pick and complete card meet the T-45 policy.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3

import mlb_official_schedule_authority as official_schedule
import mlb_three_api_policy as policy


VERSION = "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v1"
EASTERN = ZoneInfo("America/New_York")

lambda_client = boto3.client("lambda")

PULL_FUNCTION_NAME = os.environ.get("MLB_THREE_API_PULL_FUNCTION_NAME", "")
LOCK_FUNCTION_NAME = os.environ.get("MLB_THREE_API_LOCK_FUNCTION_NAME", "")
TRAIN_FUNCTION_NAME = os.environ.get("MLB_THREE_API_TRAIN_FUNCTION_NAME", "")
VERIFY_FUNCTION_NAME = os.environ.get("MLB_THREE_API_VERIFY_FUNCTION_NAME", "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invoke(function_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not function_name:
        return {"ok": False, "status": "NOT_CONFIGURED"}
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    raw = response.get("Payload").read() if response.get("Payload") else b"{}"
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        body = {"raw": raw.decode("utf-8", errors="replace")[:2000]}
    if response.get("FunctionError"):
        return {
            "ok": False,
            "status": "FUNCTION_ERROR",
            "functionError": response.get("FunctionError"),
            "response": body,
        }
    if isinstance(body, dict) and "body" in body and isinstance(body.get("body"), str):
        try:
            parsed = json.loads(body["body"])
            if isinstance(parsed, dict):
                body = parsed
        except Exception:
            pass
    return {"ok": True, "status": "INVOKED", "response": body}


def _slate_date_et(now: datetime) -> str:
    return now.astimezone(EASTERN).date().isoformat()


def _schedule(now: datetime) -> Dict[str, Any]:
    date = _slate_date_et(now)
    return official_schedule.fetch_exact_date_schedule(date)


def _lock_payload(schedule: Dict[str, Any], deadlines: policy.CardDeadlines) -> Dict[str, Any]:
    return {
        "action": "lock_daily_card",
        "source": "mlb-three-api-autonomous-controller",
        "policyVersion": policy.POLICY_VERSION,
        "slateDateEt": deadlines.slate_date_et,
        "predictionLeadMinutes": policy.PREDICTION_LEAD_MINUTES,
        "requireAllOfficialGames": True,
        "noPasses": True,
        "firstGamePredictionDeadlineUtc": deadlines.first_game_prediction_deadline_utc.isoformat(),
        "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
        "officialGameCount": schedule.get("officialGameCount"),
        "officialGameIds": schedule.get("officialGameIds"),
        "threeApiRequired": ["MLB_STATS_API", "THE_ODDS_API", "BIG_BALLS_DATA_PRO"],
        "llmAnalystRequired": True,
    }


def _extract_picks(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("picks", "predictions", "dailyPicks", "daily_picks"):
            rows = value.get(key)
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
        for item in value.values():
            rows = _extract_picks(item)
            if rows:
                return rows
    elif isinstance(value, list):
        for item in value:
            rows = _extract_picks(item)
            if rows:
                return rows
    return []


def _published_at(value: Any, fallback: datetime) -> str:
    if isinstance(value, dict):
        for key in (
            "publishedAtUtc", "published_at_utc", "lockedAtUtc", "locked_at_utc",
            "predictionPersistedAtUtc", "createdAtUtc", "checkedAtUtc",
        ):
            if value.get(key):
                return str(value[key])
        for item in value.values():
            found = _published_at(item, fallback)
            if found != fallback.isoformat():
                return found
    return fallback.isoformat()


def _should_pull(now_et: datetime, first_start: datetime) -> bool:
    if now_et.hour < 1:
        return False
    if now_et.astimezone(timezone.utc) >= first_start:
        return False
    return now_et.minute % 15 < 5


def _should_train(now_et: datetime, first_start: datetime) -> bool:
    if now_et.astimezone(timezone.utc) >= first_start:
        return False
    # One bounded daily request; the trainer remains autonomous internally.
    return now_et.hour == 3 and now_et.minute < 5


def run_cycle(event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = event or {}
    now = _now()
    schedule = _schedule(now)
    games = schedule.get("games") or []
    deadlines = policy.card_deadlines(games)
    now_et = now.astimezone(EASTERN)

    status: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "policyVersion": policy.POLICY_VERSION,
        "checkedAtUtc": now.isoformat(),
        "slateDateEt": deadlines.slate_date_et,
        "officialGameCount": schedule.get("officialGameCount"),
        "firstGameStartUtc": deadlines.first_game_start_utc.isoformat(),
        "secondGameStartUtc": deadlines.second_game_start_utc.isoformat(),
        "firstGamePredictionDeadlineUtc": deadlines.first_game_prediction_deadline_utc.isoformat(),
        "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
        "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
        "actions": {},
    }

    status_only = event.get("action") in {"status", "status_only", "health"}
    if status_only:
        verification = _invoke(
            VERIFY_FUNCTION_NAME,
            {
                "action": "status",
                "source": "mlb-three-api-autonomous-controller",
                "slateDateEt": deadlines.slate_date_et,
            },
        )
        status["actions"]["verify"] = verification
        status["operationalStatus"] = "HEALTHY" if verification.get("ok") else "DEGRADED"
        return status

    if _should_pull(now_et, deadlines.first_game_start_utc):
        status["actions"]["pull"] = _invoke(
            PULL_FUNCTION_NAME,
            {
                "action": "pull",
                "source": "mlb-three-api-autonomous-controller",
                "threeApiRequired": True,
                "slateDateEt": deadlines.slate_date_et,
            },
        )

    if _should_train(now_et, deadlines.first_game_start_utc):
        status["actions"]["train"] = _invoke(
            TRAIN_FUNCTION_NAME,
            {
                "action": "train",
                "source": "mlb-three-api-autonomous-controller",
                "threeApiRequired": True,
                "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
            },
        )

    # Keep requesting the immutable full-slate card from 01:00 ET until the
    # complete-card deadline. The canonical lock function is responsible for
    # write-once semantics; retries are therefore safe.
    before_complete_deadline = now <= deadlines.complete_card_deadline_utc
    after_collection_start = now_et.hour >= 1
    if before_complete_deadline and after_collection_start:
        lock_result = _invoke(LOCK_FUNCTION_NAME, _lock_payload(schedule, deadlines))
        status["actions"]["lock"] = lock_result
        response = lock_result.get("response") if isinstance(lock_result, dict) else {}
        picks = _extract_picks(response)
        if picks:
            card_check = policy.validate_daily_card(
                games,
                picks,
                card_published_at_utc=_published_at(response, now),
            )
            status["cardPolicy"] = card_check
            if not card_check.get("ok"):
                status["ok"] = False

    status["actions"]["verify"] = _invoke(
        VERIFY_FUNCTION_NAME,
        {
            "action": "verify_daily_card",
            "source": "mlb-three-api-autonomous-controller",
            "slateDateEt": deadlines.slate_date_et,
            "officialGameCount": schedule.get("officialGameCount"),
            "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
            "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
        },
    )
    status["operationalStatus"] = "HEALTHY" if status["ok"] else "DEGRADED"
    return status


def lambda_handler(event: Optional[Dict[str, Any]], context: Any) -> Dict[str, Any]:
    try:
        return run_cycle(event)
    except Exception as exc:
        return {
            "ok": False,
            "version": VERSION,
            "checkedAtUtc": _now().isoformat(),
            "operationalStatus": "UNHEALTHY",
            "error": f"{type(exc).__name__}:{exc}",
        }
