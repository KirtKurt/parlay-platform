from __future__ import annotations

"""Stateful autonomous controller for MLB three-source predictions.

Every five minutes it verifies the official slate, keeps market/context evidence
fresh, asks the existing immutable lock authority for a complete no-pass card,
validates each locked row contains a three-source ensemble decision, and runs
post-settlement scoring/retraining once. The first game remains protected at its
own T-45; the complete card is due by the second official game T-45.
"""

import copy
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import boto3

import mlb_official_schedule_authority as official_schedule
import mlb_three_api_policy as policy


VERSION = "MLB-THREE-API-AUTONOMOUS-CONTROLLER-v2"
EASTERN = ZoneInfo("America/New_York")
STATE_TABLE_NAME = os.environ.get("MLB_THREE_API_STATE_TABLE", "")

lambda_client = boto3.client("lambda")
dynamodb = boto3.resource("dynamodb")
state_table = dynamodb.Table(STATE_TABLE_NAME) if STATE_TABLE_NAME else None

PULL_FUNCTION_NAME = os.environ.get("MLB_THREE_API_PULL_FUNCTION_NAME", "")
LOCK_FUNCTION_NAME = os.environ.get("MLB_THREE_API_LOCK_FUNCTION_NAME", "")
TRAIN_FUNCTION_NAME = os.environ.get("MLB_THREE_API_TRAIN_FUNCTION_NAME", "")
VERIFY_FUNCTION_NAME = os.environ.get("MLB_THREE_API_VERIFY_FUNCTION_NAME", "")
READ_FUNCTION_NAME = os.environ.get("MLB_THREE_API_READ_FUNCTION_NAME", "")


class ControllerError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _invoke(function_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not function_name:
        return {"ok": False, "status": "NOT_CONFIGURED", "payload": payload}
    try:
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"),
        )
        raw = response.get("Payload").read() if response.get("Payload") else b"{}"
        try:
            body: Any = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {"raw": raw.decode("utf-8", errors="replace")[:4000]}
        if isinstance(body, dict) and isinstance(body.get("body"), str):
            try:
                decoded = json.loads(body["body"])
                if isinstance(decoded, (dict, list)):
                    body = decoded
            except Exception:
                pass
        function_error = response.get("FunctionError")
        application_ok = not (
            isinstance(body, dict)
            and body.get("ok") is False
            and str(body.get("status") or "").upper() not in {
                "PENDING", "NOT_DUE", "ALREADY_COMPLETE", "NO_CHANGE",
            }
        )
        return {
            "ok": not function_error and application_ok,
            "status": "INVOKED" if not function_error else "FUNCTION_ERROR",
            "functionName": function_name,
            "functionError": function_error,
            "request": copy.deepcopy(payload),
            "response": _plain(body),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "INVOKE_EXCEPTION",
            "functionName": function_name,
            "request": copy.deepcopy(payload),
            "error": f"{type(exc).__name__}:{exc}",
        }


def _state_key(slate_date: str) -> Dict[str, str]:
    return {"PK": f"MLB_THREE_API#{slate_date}", "SK": "AUTONOMY_STATE"}


def _load_state(slate_date: str) -> Dict[str, Any]:
    if state_table is None:
        return {}
    response = state_table.get_item(Key=_state_key(slate_date), ConsistentRead=True)
    item = response.get("Item") if isinstance(response, dict) else None
    return _plain(item.get("data") or {}) if isinstance(item, dict) else {}


def _save_state(slate_date: str, value: Dict[str, Any]) -> None:
    if state_table is None:
        return
    state_table.put_item(
        Item={
            **_state_key(slate_date),
            "recordType": "mlb_three_api_autonomy_state",
            "updatedAtUtc": _now().isoformat(),
            "data": value,
        }
    )


def _slate_date_et(now: datetime) -> str:
    return now.astimezone(EASTERN).date().isoformat()


def _fetch_schedule(now: datetime) -> Dict[str, Any]:
    return official_schedule.fetch_exact_date_schedule(_slate_date_et(now))


def _extract_picks(value: Any, *, depth: int = 0) -> List[Dict[str, Any]]:
    if depth > 10:
        return []
    if isinstance(value, dict):
        for key in (
            "picks", "predictions", "dailyPicks", "daily_picks", "lockedPicks",
            "locked_picks", "officialPicks", "official_picks", "games",
        ):
            rows = value.get(key)
            if isinstance(rows, list):
                candidates = [row for row in rows if isinstance(row, dict)]
                if candidates and any(
                    row.get("predictedWinner") or row.get("predicted_winner")
                    for row in candidates
                ):
                    return candidates
        for item in value.values():
            rows = _extract_picks(item, depth=depth + 1)
            if rows:
                return rows
    elif isinstance(value, list):
        candidates = [row for row in value if isinstance(row, dict)]
        if candidates and any(
            row.get("predictedWinner") or row.get("predicted_winner")
            for row in candidates
        ):
            return candidates
        for item in value:
            rows = _extract_picks(item, depth=depth + 1)
            if rows:
                return rows
    return []


def _find_timestamp(value: Any, fallback: datetime, *, depth: int = 0) -> str:
    if depth > 8:
        return fallback.isoformat()
    if isinstance(value, dict):
        for key in (
            "publishedAtUtc", "published_at_utc", "lockedAtUtc", "locked_at_utc",
            "predictionPersistedAtUtc", "prediction_persisted_at_utc", "createdAtUtc",
            "generatedAtUtc", "observedAtUtc", "checkedAtUtc",
        ):
            if value.get(key):
                return str(value[key])
        for item in value.values():
            found = _find_timestamp(item, fallback, depth=depth + 1)
            if found != fallback.isoformat():
                return found
    return fallback.isoformat()


def _three_api_pick_errors(picks: Iterable[Dict[str, Any]]) -> List[str]:
    errors: List[str] = []
    for row in picks:
        game_id = str(
            row.get("official_game_pk")
            or row.get("officialGamePk")
            or row.get("game_id")
            or row.get("gameId")
            or row.get("id")
            or "UNKNOWN"
        )
        decision = row.get("threeApiDecision") if isinstance(row.get("threeApiDecision"), dict) else {}
        if decision.get("version") != "MLB-THREE-API-PREDICTION-ENSEMBLE-v1":
            errors.append(f"THREE_API_DECISION_MISSING:{game_id}")
            continue
        ready = decision.get("sourceReady") if isinstance(decision.get("sourceReady"), dict) else {}
        for source in ("mlbStatsApi", "theOddsApi", "bigBallsDataPro", "bedrockLlm"):
            if ready.get(source) is not True:
                errors.append(f"THREE_API_SOURCE_NOT_READY:{game_id}:{source}")
        components = decision.get("components") if isinstance(decision.get("components"), list) else []
        names = {str(component.get("component") or "") for component in components if isinstance(component, dict)}
        if "bedrockThreeSourceAnalyst" not in names:
            errors.append(f"LLM_NOT_IN_FINAL_ENSEMBLE:{game_id}")
        if "theOddsApiNoVigMarketConsensus" not in names:
            errors.append(f"MARKET_NOT_IN_FINAL_ENSEMBLE:{game_id}")
        if not decision.get("llmEvidenceFingerprint"):
            errors.append(f"LLM_EVIDENCE_FINGERPRINT_MISSING:{game_id}")
        if not decision.get("bbdContextFingerprint"):
            errors.append(f"BBD_CONTEXT_FINGERPRINT_MISSING:{game_id}")
        if decision.get("noPass") is not True:
            errors.append(f"NO_PASS_CONTRACT_MISSING:{game_id}")
    return sorted(set(errors))


def _lock_base(schedule: Dict[str, Any], deadlines: policy.CardDeadlines) -> Dict[str, Any]:
    return {
        "source": "mlb-three-api-autonomous-controller-v2",
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
        "threeApiDecisionVersion": "MLB-THREE-API-PREDICTION-ENSEMBLE-v1",
    }


def _lock_attempts(schedule: Dict[str, Any], deadlines: policy.CardDeadlines) -> List[Dict[str, Any]]:
    base = _lock_base(schedule, deadlines)
    return [
        {**base, "action": "lock_daily_card"},
        {**base, "action": "run"},
        {**base, "action": "scheduled"},
        {**base, "operation": "lock", "scheduled": True},
        {
            "version": "0",
            "id": f"mlb-three-api-{deadlines.slate_date_et}",
            "detail-type": "Scheduled Event",
            "source": "aws.events",
            "time": _now().isoformat(),
            "detail": base,
        },
        base,
    ]


def _invoke_lock_until_card(schedule: Dict[str, Any], deadlines: policy.CardDeadlines) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for payload in _lock_attempts(schedule, deadlines):
        result = _invoke(LOCK_FUNCTION_NAME, payload)
        attempts.append(result)
        picks = _extract_picks(result.get("response"))
        if picks:
            return {"ok": result.get("ok"), "attempts": attempts, "picks": picks, "source": "lock"}
    # Some lock handlers return only metadata. Query the verifier and read
    # function for the immutable card without assuming a single API shape.
    for function_name, payloads in (
        (
            VERIFY_FUNCTION_NAME,
            (
                {"action": "verify_daily_card", **_lock_base(schedule, deadlines)},
                {"action": "status", "slateDateEt": deadlines.slate_date_et},
            ),
        ),
        (
            READ_FUNCTION_NAME,
            (
                {"action": "daily_card", "slateDateEt": deadlines.slate_date_et},
                {"path": "/v1/mlb/picks", "httpMethod": "GET", "queryStringParameters": {"date": deadlines.slate_date_et}},
                {"path": "/v1/mlb/predictions", "httpMethod": "GET", "queryStringParameters": {"date": deadlines.slate_date_et}},
            ),
        ),
    ):
        for payload in payloads:
            result = _invoke(function_name, payload)
            attempts.append(result)
            picks = _extract_picks(result.get("response"))
            if picks:
                return {"ok": result.get("ok"), "attempts": attempts, "picks": picks, "source": function_name}
    return {"ok": False, "attempts": attempts, "picks": [], "source": None}


def _game_final(game: Mapping[str, Any]) -> bool:
    status = game.get("official_status") or game.get("officialStatus") or {}
    if not isinstance(status, Mapping):
        return False
    return str(status.get("abstractGameState") or "").upper() == "FINAL"


def _all_games_final(games: Sequence[Dict[str, Any]]) -> bool:
    return bool(games) and all(_game_final(game) for game in games)


def _should_pull(now_et: datetime, first_start: datetime) -> bool:
    return now_et.hour >= 1 and now_et.astimezone(timezone.utc) < first_start and now_et.minute % 15 < 5


def _should_pretrain(now_et: datetime, first_start: datetime, state: Mapping[str, Any]) -> bool:
    return (
        not state.get("preSlateTrainingCompleted")
        and now_et.hour >= 3
        and now_et.astimezone(timezone.utc) < first_start
    )


def _extract_accuracy(value: Any, *, depth: int = 0) -> Optional[float]:
    if depth > 10:
        return None
    if isinstance(value, dict):
        for key in (
            "dailyAccuracy", "daily_accuracy", "accuracy", "correctPickPercentage",
            "correct_pick_percentage", "winRate", "win_rate",
        ):
            raw = value.get(key)
            try:
                number = float(raw)
                if number > 1.0 and number <= 100.0:
                    number /= 100.0
                if 0.0 <= number <= 1.0:
                    return number
            except Exception:
                pass
        for item in value.values():
            found = _extract_accuracy(item, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_accuracy(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def run_cycle(event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    event = event or {}
    now = _now()
    schedule = _fetch_schedule(now)
    games = [row for row in schedule.get("games") or [] if isinstance(row, dict)]
    deadlines = policy.card_deadlines(games)
    now_et = now.astimezone(EASTERN)
    state = _load_state(deadlines.slate_date_et)

    report: Dict[str, Any] = {
        "ok": True,
        "version": VERSION,
        "policyVersion": policy.POLICY_VERSION,
        "checkedAtUtc": now.isoformat(),
        "slateDateEt": deadlines.slate_date_et,
        "officialGameCount": schedule.get("officialGameCount"),
        "officialGameIds": schedule.get("officialGameIds"),
        "firstGameStartUtc": deadlines.first_game_start_utc.isoformat(),
        "secondGameStartUtc": deadlines.second_game_start_utc.isoformat(),
        "firstGamePredictionDeadlineUtc": deadlines.first_game_prediction_deadline_utc.isoformat(),
        "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
        "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
        "accuracyGuarantee": False,
        "stateBefore": copy.deepcopy(state),
        "actions": {},
    }

    if event.get("action") in {"status", "status_only", "health"}:
        verify = _invoke(
            VERIFY_FUNCTION_NAME,
            {
                "action": "status",
                "source": "mlb-three-api-autonomous-controller-v2",
                "slateDateEt": deadlines.slate_date_et,
            },
        )
        report["actions"]["verify"] = verify
        report["operationalStatus"] = "HEALTHY" if verify.get("ok") else "DEGRADED"
        return report

    if _should_pull(now_et, deadlines.first_game_start_utc):
        report["actions"]["pull"] = _invoke(
            PULL_FUNCTION_NAME,
            {
                "action": "pull",
                "source": "mlb-three-api-autonomous-controller-v2",
                "threeApiRequired": True,
                "slateDateEt": deadlines.slate_date_et,
            },
        )

    if _should_pretrain(now_et, deadlines.first_game_start_utc, state):
        training = _invoke(
            TRAIN_FUNCTION_NAME,
            {
                "action": "train",
                "source": "mlb-three-api-autonomous-controller-v2-pre-slate",
                "threeApiRequired": True,
                "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
                "autonomous": True,
            },
        )
        report["actions"]["preSlateTraining"] = training
        if training.get("ok"):
            state["preSlateTrainingCompleted"] = True
            state["preSlateTrainingAtUtc"] = now.isoformat()

    card_accepted = bool(state.get("cardAccepted"))
    collection_started = now_et.hour >= 1
    if collection_started and not card_accepted and now <= deadlines.complete_card_deadline_utc:
        card = _invoke_lock_until_card(schedule, deadlines)
        report["actions"]["card"] = card
        picks = card.get("picks") or []
        if picks:
            published_at = _find_timestamp(card, now)
            validation = policy.validate_daily_card(
                games,
                picks,
                card_published_at_utc=published_at,
            )
            validation["threeApiErrors"] = _three_api_pick_errors(picks)
            validation["threeApiComplete"] = not validation["threeApiErrors"]
            validation["ok"] = bool(validation.get("ok") and validation["threeApiComplete"])
            report["cardPolicy"] = validation
            if validation["ok"]:
                state.update(
                    {
                        "cardAccepted": True,
                        "cardAcceptedAtUtc": now.isoformat(),
                        "cardPublishedAtUtc": published_at,
                        "cardPredictionCount": len(picks),
                        "cardPolicyVersion": policy.POLICY_VERSION,
                        "threeApiDecisionVersion": "MLB-THREE-API-PREDICTION-ENSEMBLE-v1",
                    }
                )
                card_accepted = True

    if now > deadlines.complete_card_deadline_utc and not card_accepted:
        report["ok"] = False
        report["deadlineViolation"] = "COMPLETE_CARD_NOT_PROVEN_BY_SECOND_GAME_T45"

    verify = _invoke(
        VERIFY_FUNCTION_NAME,
        {
            "action": "verify_daily_card",
            "source": "mlb-three-api-autonomous-controller-v2",
            "slateDateEt": deadlines.slate_date_et,
            "officialGameCount": schedule.get("officialGameCount"),
            "completeCardDeadlineUtc": deadlines.complete_card_deadline_utc.isoformat(),
            "threeApiDecisionRequired": True,
            "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
        },
    )
    report["actions"]["verify"] = verify

    if _all_games_final(games) and not state.get("postSettlementCycleCompleted"):
        settlement = _invoke(
            VERIFY_FUNCTION_NAME,
            {
                "action": "settle_and_score",
                "source": "mlb-three-api-autonomous-controller-v2",
                "slateDateEt": deadlines.slate_date_et,
                "officialGameCount": schedule.get("officialGameCount"),
                "fullOfficialSlateDenominator": True,
                "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
            },
        )
        retrain = _invoke(
            TRAIN_FUNCTION_NAME,
            {
                "action": "train",
                "source": "mlb-three-api-autonomous-controller-v2-post-settlement",
                "slateDateEt": deadlines.slate_date_et,
                "threeApiRequired": True,
                "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
                "fullOfficialSlateDenominator": True,
                "autonomous": True,
            },
        )
        report["actions"]["settlement"] = settlement
        report["actions"]["postSettlementTraining"] = retrain
        accuracy = _extract_accuracy(settlement.get("response"))
        state.update(
            {
                "postSettlementCycleCompleted": bool(settlement.get("ok") and retrain.get("ok")),
                "postSettlementCycleAtUtc": now.isoformat(),
                "dailyAccuracy": accuracy,
                "dailyAccuracyGoal": policy.DAILY_ACCURACY_GOAL,
                "dailyAccuracyGoalMet": bool(accuracy is not None and accuracy >= policy.DAILY_ACCURACY_GOAL),
            }
        )

    _save_state(deadlines.slate_date_et, state)
    report["stateAfter"] = copy.deepcopy(state)
    report["operationalStatus"] = (
        "UNHEALTHY" if not report["ok"]
        else "HEALTHY" if state.get("cardAccepted")
        else "PENDING_BEFORE_DEADLINE"
    )
    return report


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
