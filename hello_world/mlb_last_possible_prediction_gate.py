from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

try:
    import inqsi_pull_history as history
except Exception:
    history = None

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
FINAL_GATE_START_MINUTES = int(os.environ.get("INQSI_MLB_FINAL_GATE_START_MINUTES", "720"))
FINAL_GATE_END_MINUTES = int(os.environ.get("INQSI_MLB_FINAL_GATE_END_MINUTES", "10"))
REQUIRE_SPORTSDATAIO_AT_FINAL_GATE = os.environ.get("INQSI_REQUIRE_SPORTSDATAIO_FINAL_GATE", "false").lower() in {"1", "true", "yes"}

POLICY_VERSION_REQUIRE_SPORTSDATAIO = "MLB-LAST-POSSIBLE-PREDICTION-GATE-v4-12H-INDIVIDUAL-GAME-REQUIRE-SPORTSDATAIO"
POLICY_VERSION_ODDS_API_ONLY = "MLB-LAST-POSSIBLE-PREDICTION-GATE-v4-12H-INDIVIDUAL-GAME-ODDS-API-ONLY"
SLATE_LOCK_POLICY_VERSION = "MLB-SLATE-WIDE-PREDICTION-LOCK-v1-45MIN"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _minutes_to_start(row: Dict[str, Any]) -> float | None:
    dt = _parse_dt(row.get("commenceTime") or row.get("commence_time"))
    if not dt:
        return None
    return round((dt - _now_utc()).total_seconds() / 60.0, 2)


def _phase(minutes_to_start: float | None) -> str:
    if minutes_to_start is None:
        return "UNKNOWN_START_TIME"
    if minutes_to_start < 0:
        return "GAME_STARTED_OR_CLOSED"
    if minutes_to_start < FINAL_GATE_END_MINUTES:
        return "LOCK_CLOSED"
    if minutes_to_start <= FINAL_GATE_START_MINUTES:
        return "FINAL_GATE_OPEN"
    return "PRE_FINAL_GATE"


def _store_final(row: Dict[str, Any]) -> Dict[str, Any]:
    if history is None or history.PULLS is None:
        return {"ok": False, "error": "SNAPSHOTS_TABLE not configured"}
    try:
        gate = row.get("lastPossiblePredictionGate") or {}
        item = history.ddb_safe({
            "PK": f"GAME_WINNERS#mlb#{row.get('slate_date')}",
            "SK": f"GAME#{row.get('commenceTime') or 'unknown'}#{row.get('gameId')}",
            "record_type": "mlb_game_winner_prediction",
            "sport": "mlb",
            "slate_date": row.get("slate_date"),
            "game_id": row.get("gameId"),
            "game_key": row.get("gameKey"),
            "predicted_winner": row.get("predictedWinner"),
            "confidence_tier": row.get("confidenceTier"),
            "score": row.get("score"),
            "win_probability": row.get("winProbability"),
            "created_at": row.get("createdAt"),
            "final_gate_phase": gate.get("phase"),
            "final_gate_locked": gate.get("finalLocked"),
            "final_gate_blocked": gate.get("finalGateBlocked"),
            "fundamentals_applied": gate.get("sportsDataIoFundamentalsApplied"),
            "odds_api_only": gate.get("oddsApiOnly"),
            "slate_wide_lock": gate.get("slateWideLock"),
            "slate_lock_at_utc": gate.get("lockAtUtc"),
            "data": row,
        })
        history.PULLS.put_item(Item=item)
        return {"ok": True, "pk": item["PK"], "sk": item["SK"], "finalGateStored": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _annotate_from_slate_lock(out: Dict[str, Any], persist: bool = False) -> Dict[str, Any]:
    lock = dict(out.get("slatePredictionLock") or {})
    optimizer = out.get("winnerOptimizer") or {}
    fundamentals_applied = bool(optimizer.get("fundamentalsApplied"))
    final_locked = bool(lock.get("locked"))
    phase = "SLATE_LOCKED" if final_locked else "PRE_SLATE_LOCK"
    blocked_missing_sportsdataio = bool(REQUIRE_SPORTSDATAIO_AT_FINAL_GATE and final_locked and not fundamentals_applied)
    policy_version = lock.get("policyVersion") or SLATE_LOCK_POLICY_VERSION
    source = "MARKET_PLUS_MULTI_WINDOW_LEARNING_PLUS_SPORTSDATAIO_SLATE_LOCK" if fundamentals_applied else "MARKET_PLUS_MULTI_WINDOW_LEARNING_ODDS_API_ONLY_SLATE_LOCK"
    final_data_status = "FULL_DATA_READY" if fundamentals_applied else "ODDS_API_ONLY_READY"
    if blocked_missing_sportsdataio:
        source = "BLOCKED_FULL_DATA_PICK_MISSING_SPORTSDATAIO_SLATE_LOCK"
        final_data_status = "BLOCKED_MISSING_SPORTSDATAIO"

    out["lastPossiblePredictionGate"] = {
        "policyVersion": policy_version,
        "phase": phase,
        "minutesToStart": lock.get("minutesUntilFirstGameStart"),
        "gateWindowMinutesBeforeStart": {
            "opensAt": lock.get("lockMinutesBeforeFirstGame", 45),
            "closesAt": lock.get("lockMinutesBeforeFirstGame", 45),
            "meaning": "single_slate_wide_lock_cutoff",
        },
        "slateWideLock": True,
        "lockMinutesBeforeFirstGame": lock.get("lockMinutesBeforeFirstGame", 45),
        "firstGameStartUtc": lock.get("firstGameStartUtc"),
        "lockAtUtc": lock.get("lockAtUtc"),
        "latestAvailablePullAt": lock.get("latestAvailablePullAt"),
        "latestScoringPullAt": lock.get("latestScoringPullAt"),
        "totalPullCountAvailable": lock.get("totalPullCountAvailable"),
        "scoringPullCount": lock.get("scoringPullCount"),
        "finalWindowActive": final_locked,
        "finalLocked": final_locked,
        "oddsApiOnly": not REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "requiresSportsDataIoAttempt": REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "requiresSportsDataIoForFullDataFinalPick": REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "sportsDataIoFundamentalsApplied": fundamentals_applied,
        "finalGateBlocked": blocked_missing_sportsdataio,
        "finalGateBlockReason": "SPORTSDATAIO_FINAL_GATE_MISSING" if blocked_missing_sportsdataio else None,
        "finalDataStatus": final_data_status,
        "predictionSource": source,
        "rules": [
            "All MLB predictions for the slate lock together 45 minutes before the first game begins.",
            "Once the slate is locked, later 15-minute pulls do not change any game-winner prediction on that slate.",
            "Locked predictions are scored from pull history captured at or before the slate lock timestamp.",
        ],
    }
    tags = sorted(set(out.get("tags") or []))
    tags.append("SLATE_WIDE_45_MIN_LOCK_POLICY")
    if final_locked:
        tags.extend(["SLATE_LOCKED", "FINAL_LOCKED"])
        out["fullDataFinalPick"] = True
    else:
        tags.append("PRE_SLATE_LOCK")
    if blocked_missing_sportsdataio:
        tags.append("FINAL_GATE_BLOCKED_MISSING_SPORTSDATAIO")
        out["fullDataFinalPick"] = False
        out["officialPick"] = False
        out["accuracyTargetEligible"] = False
        out["actionability"] = "BLOCKED_MISSING_SPORTSDATAIO_SLATE_LOCK"
        out["actionabilityReason"] = "sportsdataio_required_for_full_data_slate_lock_but_not_applied"
    out["tags"] = sorted(set(tags))
    if persist:
        out["finalGateStored"] = _store_final(out)
    return out


def annotate_prediction(row: Dict[str, Any], persist: bool = False) -> Dict[str, Any]:
    out = dict(row or {})
    if (out.get("slatePredictionLock") or {}).get("slateWideLock"):
        return _annotate_from_slate_lock(out, persist=persist)

    minutes = _minutes_to_start(out)
    phase = _phase(minutes)
    optimizer = out.get("winnerOptimizer") or {}
    fundamentals_applied = bool(optimizer.get("fundamentalsApplied"))
    final_window = phase == "FINAL_GATE_OPEN"
    closed = phase in {"LOCK_CLOSED", "GAME_STARTED_OR_CLOSED"}
    final_locked = final_window or closed
    blocked_missing_sportsdataio = bool(REQUIRE_SPORTSDATAIO_AT_FINAL_GATE and final_locked and not fundamentals_applied)

    if REQUIRE_SPORTSDATAIO_AT_FINAL_GATE and fundamentals_applied:
        source = "MARKET_PLUS_MULTI_WINDOW_LEARNING_PLUS_SPORTSDATAIO_FINAL_GATE"
        final_data_status = "FULL_DATA_READY"
        policy_version = POLICY_VERSION_REQUIRE_SPORTSDATAIO
    elif blocked_missing_sportsdataio:
        source = "BLOCKED_FULL_DATA_PICK_MISSING_SPORTSDATAIO_FINAL_GATE"
        final_data_status = "BLOCKED_MISSING_SPORTSDATAIO"
        policy_version = POLICY_VERSION_REQUIRE_SPORTSDATAIO
    else:
        source = "MARKET_PLUS_MULTI_WINDOW_LEARNING_ODDS_API_ONLY_FINAL_GATE"
        final_data_status = "ODDS_API_ONLY_READY"
        policy_version = POLICY_VERSION_ODDS_API_ONLY

    out["lastPossiblePredictionGate"] = {
        "policyVersion": policy_version,
        "phase": phase,
        "minutesToStart": minutes,
        "gateWindowMinutesBeforeStart": {"opensAt": FINAL_GATE_START_MINUTES, "closesAt": FINAL_GATE_END_MINUTES},
        "finalWindowActive": final_window,
        "finalLocked": final_locked,
        "oddsApiOnly": not REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "requiresSportsDataIoAttempt": REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "requiresSportsDataIoForFullDataFinalPick": REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "sportsDataIoFundamentalsApplied": fundamentals_applied,
        "finalGateBlocked": blocked_missing_sportsdataio,
        "finalGateBlockReason": "SPORTSDATAIO_FINAL_GATE_MISSING" if blocked_missing_sportsdataio else None,
        "finalDataStatus": final_data_status,
        "predictionSource": source,
        "rules": [
            f"Open the final gate {FINAL_GATE_START_MINUTES} minutes before each individual game.",
            "This fallback is only used when the slate-wide lock wrapper is absent.",
        ],
    }
    tags = sorted(set(out.get("tags") or []))
    if final_window:
        tags.append("FINAL_GATE_OPEN")
    if final_locked:
        tags.append("FINAL_LOCKED")
    if not REQUIRE_SPORTSDATAIO_AT_FINAL_GATE:
        tags.append("ODDS_API_ONLY")
    if blocked_missing_sportsdataio:
        tags.append("FINAL_GATE_BLOCKED_MISSING_SPORTSDATAIO")
        out["fullDataFinalPick"] = False
        out["officialPick"] = False
        out["accuracyTargetEligible"] = False
        out["actionability"] = "BLOCKED_MISSING_SPORTSDATAIO_FINAL_GATE"
        out["actionabilityReason"] = "sportsdataio_required_for_full_data_final_gate_but_not_applied"
    elif final_locked:
        out["fullDataFinalPick"] = True
    out["tags"] = sorted(set(tags))
    if persist:
        out["finalGateStored"] = _store_final(out)
    return out


def annotate_result(result: Dict[str, Any], persist: bool = False) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    predictions = [annotate_prediction(row, persist=persist) for row in (result.get("predictions") or [])]
    predictions.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for idx, row in enumerate(predictions, 1):
        row["rank"] = idx
    phases: Dict[str, int] = {}
    for row in predictions:
        phase = (row.get("lastPossiblePredictionGate") or {}).get("phase") or "UNKNOWN"
        phases[phase] = phases.get(phase, 0) + 1
    fundamentals_applied = [row for row in predictions if (row.get("lastPossiblePredictionGate") or {}).get("sportsDataIoFundamentalsApplied")]
    final_rows = [row for row in predictions if (row.get("lastPossiblePredictionGate") or {}).get("finalLocked")]
    blocked_rows = [row for row in predictions if (row.get("lastPossiblePredictionGate") or {}).get("finalGateBlocked")]
    full_data_rows = [row for row in predictions if row.get("fullDataFinalPick")]
    summary = dict(result.get("rolling24hAccuracyTarget") or result.get("accuracyTarget") or {})
    lock = result.get("slatePredictionLock") or ((predictions[0].get("slatePredictionLock") if predictions else {}) or {})
    if lock.get("slateWideLock"):
        policy_version = lock.get("policyVersion") or SLATE_LOCK_POLICY_VERSION
        gate_window = {"opensAt": lock.get("lockMinutesBeforeFirstGame", 45), "closesAt": lock.get("lockMinutesBeforeFirstGame", 45), "meaning": "single_slate_wide_lock_cutoff"}
    else:
        policy_version = POLICY_VERSION_REQUIRE_SPORTSDATAIO if REQUIRE_SPORTSDATAIO_AT_FINAL_GATE else POLICY_VERSION_ODDS_API_ONLY
        gate_window = {"opensAt": FINAL_GATE_START_MINUTES, "closesAt": FINAL_GATE_END_MINUTES}
    summary["lastPossiblePredictionGate"] = {
        "applied": True,
        "policyVersion": policy_version,
        "slateWideLock": bool(lock.get("slateWideLock")),
        "lockAtUtc": lock.get("lockAtUtc"),
        "firstGameStartUtc": lock.get("firstGameStartUtc"),
        "latestScoringPullAt": lock.get("latestScoringPullAt"),
        "gateWindowMinutesBeforeStart": gate_window,
        "phaseCounts": phases,
        "finalLockedCount": len(final_rows),
        "oddsApiOnly": not REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "requiresSportsDataIoForFullDataFinalPick": REQUIRE_SPORTSDATAIO_AT_FINAL_GATE,
        "sportsDataIoFundamentalsAppliedCount": len(fundamentals_applied),
        "sportsDataIoMissingFinalGateCount": len([row for row in final_rows if REQUIRE_SPORTSDATAIO_AT_FINAL_GATE and not (row.get("lastPossiblePredictionGate") or {}).get("sportsDataIoFundamentalsApplied")]),
        "blockedMissingSportsDataIoCount": len(blocked_rows),
        "fullDataFinalPickCount": len(full_data_rows),
        "persistedFinalRows": bool(persist),
    }
    out = dict(result)
    out["predictions"] = predictions
    out["count"] = len(predictions)
    out["lastPossiblePredictionGate"] = summary["lastPossiblePredictionGate"]
    out["rolling24hAccuracyTarget"] = summary
    out["accuracyTarget"] = summary
    suffix = "+slate-wide-45min-final-gate" if lock.get("slateWideLock") else ("+last-possible-gate-v4-12h-individual-game-require-sportsdataio" if REQUIRE_SPORTSDATAIO_AT_FINAL_GATE else "+last-possible-gate-v4-12h-individual-game-odds-api-only")
    if suffix not in str(result.get("modelVersion") or ""):
        out["modelVersion"] = str(result.get("modelVersion") or "") + suffix
    return out


def apply(module):
    if getattr(module, "_INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED", False):
        return module
    original_predict_all = module.predict_all

    def patched_predict_all(*args, **kwargs):
        persist = bool(kwargs.get("store"))
        result = original_predict_all(*args, **kwargs)
        return annotate_result(result, persist=persist)

    module.predict_all = patched_predict_all
    module._INQSI_MLB_LAST_POSSIBLE_GATE_APPLIED = True
    return module
