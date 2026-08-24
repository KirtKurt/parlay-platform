from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import boto3

import mlb_daily_card_deadline_v1 as deadline_policy
import mlb_official_schedule_authority as official_schedule


VERSION = "MLB-AUTONOMOUS-ORCHESTRATOR-v1-five-minute-second-game-t45"
SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
LEAD_MINUTES = max(45, int(os.environ.get("MLB_CARD_LEAD_MINUTES", "45")))
COLLECTION_START_HOURS = max(1, int(os.environ.get("MLB_COLLECTION_START_HOURS", "12")))
DAILY_ACCURACY_TARGET = float(os.environ.get("MLB_DAILY_ACCURACY_TARGET", "0.70"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    return deadline_policy.parse_dt(value)


def _slate_date(now: datetime) -> str:
    return now.astimezone(SLATE_TZ).date().isoformat()


def _invoke(
    client: Any,
    function_name: str,
    payload: Dict[str, Any],
    *,
    invocation_type: str = "RequestResponse",
) -> Dict[str, Any]:
    if not function_name:
        return {"invoked": False, "status": "FUNCTION_NOT_CONFIGURED"}
    response = client.invoke(
        FunctionName=function_name,
        InvocationType=invocation_type,
        Payload=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )
    output: Any = None
    stream = response.get("Payload")
    if stream is not None:
        raw = stream.read() if hasattr(stream, "read") else stream
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if raw not in (None, ""):
            try:
                output = json.loads(raw)
            except Exception:
                output = str(raw)[:2000]
    if response.get("FunctionError"):
        raise RuntimeError(
            f"AUTONOMOUS_TARGET_FUNCTION_ERROR:{function_name}:{response.get('FunctionError')}:{output}"
        )
    return {
        "invoked": True,
        "functionName": function_name,
        "statusCode": response.get("StatusCode"),
        "executedVersion": response.get("ExecutedVersion"),
        "output": output,
    }


def _phase(now: datetime, timing: deadline_policy.DailyCardDeadlines) -> str:
    if timing.game_count == 0 or timing.first_game_start_utc is None:
        return "OFFICIAL_EMPTY_SLATE"
    collection_start = timing.first_game_start_utc - timedelta(hours=COLLECTION_START_HOURS)
    if now < collection_start:
        return "BEFORE_COLLECTION_WINDOW"
    if timing.first_pick_deadline_utc and now < timing.first_pick_deadline_utc:
        return "COLLECT_AND_PREPARE"
    if timing.full_card_deadline_utc and now < timing.full_card_deadline_utc:
        return "FIRST_PICK_DUE_PREPARE_FULL_CARD"
    full_anchor_start = timing.second_game_start_utc or timing.first_game_start_utc
    if full_anchor_start and now < full_anchor_start:
        return "FULL_CARD_DUE"
    return "NO_POST_START_RECOMPUTE"


def lambda_handler(event: Any, context: Any) -> Dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    requested_now = _parse_dt(event.get("nowUtc") or event.get("now"))
    now = requested_now or _now()
    slate_date = str(event.get("slateDate") or _slate_date(now))
    schedule = official_schedule.fetch_exact_date_schedule(slate_date)
    games = schedule.get("games") or []
    timing = deadline_policy.compute_deadlines(
        games,
        slate_date=slate_date,
        lead_minutes=LEAD_MINUTES,
    )
    phase = _phase(now, timing)

    result: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "checkedAtUtc": now.isoformat(),
        "slateDate": slate_date,
        "phase": phase,
        "officialSchedule": {
            "source": schedule.get("source"),
            "sourceUrl": schedule.get("sourceUrl"),
            "verified": schedule.get("verified"),
            "officialGameCount": schedule.get("officialGameCount"),
            "officialGameIds": schedule.get("officialGameIds"),
        },
        "timing": timing.as_dict(now=now),
        "dailyAccuracyGoal": DAILY_ACCURACY_TARGET,
        "coveragePolicy": "ALL_OFFICIAL_GAMES_NO_PASS",
        "pullInvocation": None,
        "predictionInvocation": None,
    }

    if phase in {"BEFORE_COLLECTION_WINDOW", "OFFICIAL_EMPTY_SLATE", "NO_POST_START_RECOMPUTE"}:
        result["action"] = "NO_MUTATION"
        return result

    lambda_client = boto3.client(
        "lambda",
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
    )
    pull_function = str(os.environ.get("MLB_AUDITED_PULL_FUNCTION") or "").strip()
    lock_function = str(os.environ.get("MLB_DAILY_PICK_LOCK_FUNCTION") or "").strip()

    shared_payload = {
        "source": VERSION,
        "autonomous": True,
        "slateDate": slate_date,
        "slate_date": slate_date,
        "officialGameIds": schedule.get("officialGameIds") or [],
        "deadlinePolicy": "FIRST_GAME_PICK_BY_T45_AND_FULL_CARD_BY_SECOND_GAME_T45",
        "leadMinutes": LEAD_MINUTES,
        "firstPickDeadlineUtc": timing.first_pick_deadline_utc.isoformat() if timing.first_pick_deadline_utc else None,
        "fullCardDeadlineUtc": timing.full_card_deadline_utc.isoformat() if timing.full_card_deadline_utc else None,
        "forceFullSlate": phase == "FULL_CARD_DUE",
        "force_full_slate": phase == "FULL_CARD_DUE",
        "forcePrediction": phase in {"FIRST_PICK_DUE_PREPARE_FULL_CARD", "FULL_CARD_DUE"},
        "force_prediction": phase in {"FIRST_PICK_DUE_PREPARE_FULL_CARD", "FULL_CARD_DUE"},
        "dailyAccuracyGoal": DAILY_ACCURACY_TARGET,
        "allGamesNoPass": True,
        "requestedAtUtc": now.isoformat(),
    }

    # Collection is invoked first so the predictor sees the freshest admissible
    # pregame market snapshot. The downstream immutable lock remains the final
    # authority and rejects any post-start rewrite.
    if pull_function:
        result["pullInvocation"] = _invoke(
            lambda_client,
            pull_function,
            {**shared_payload, "operation": "autonomous_pregame_collection"},
        )

    result["predictionInvocation"] = _invoke(
        lambda_client,
        lock_function,
        {**shared_payload, "operation": "autonomous_daily_winner_card"},
    )
    result["action"] = "COLLECT_THEN_PREDICT"
    return result
