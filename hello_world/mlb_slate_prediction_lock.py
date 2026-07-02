from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
LOCK_MINUTES = int(os.environ.get("INQSI_MLB_SLATE_LOCK_MINUTES_BEFORE_FIRST_GAME", "45"))
POLICY_VERSION = "MLB-SLATE-WIDE-PREDICTION-LOCK-v1-45MIN"


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _game_day(game: Dict[str, Any]) -> Optional[str]:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def _game_key(game: Dict[str, Any]) -> str:
    return str(game.get("game_key") or game.get("game_id") or game.get("id") or f"mlb|{game.get('away_team')}|{game.get('home_team')}")


def _pull_dt(pull: Dict[str, Any]) -> Optional[datetime]:
    return _parse_dt((pull or {}).get("pulled_at") or (pull or {}).get("asof") or (pull or {}).get("created_at"))


def _game_sort(game: Dict[str, Any]):
    return (_parse_dt(game.get("commence_time") or game.get("commenceTime")) or datetime.max.replace(tzinfo=timezone.utc), str(game.get("away_team") or ""), str(game.get("home_team") or ""))


def _latest_games(pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for pull in pulls or []:
        for game in pull.get("games") or []:
            if _game_day(game) == slate:
                by_key[_game_key(game)] = game
    return sorted(by_key.values(), key=_game_sort)


def _lock_state(pulls: List[Dict[str, Any]], slate: str) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    all_games = _latest_games(pulls, slate)
    starts = [dt for dt in (_parse_dt(g.get("commence_time") or g.get("commenceTime")) for g in all_games) if dt]
    first_start = min(starts) if starts else None
    lock_at = first_start - timedelta(minutes=LOCK_MINUTES) if first_start else None
    locked = bool(lock_at and now >= lock_at)
    if locked and lock_at:
        scoring = [p for p in pulls if (_pull_dt(p) and _pull_dt(p) <= lock_at)]
        source = "latest_pre_lock_pull_history" if scoring else "no_pre_lock_pull_available_fallback_to_available_history"
        scoring = scoring or pulls
    else:
        scoring = pulls
        source = "live_pre_lock_pull_history"
    latest = pulls[-1] if pulls else {}
    latest_scoring = scoring[-1] if scoring else {}
    state = {
        "applied": bool(first_start),
        "policyVersion": POLICY_VERSION,
        "slateWideLock": True,
        "lockMinutesBeforeFirstGame": LOCK_MINUTES,
        "firstGameStartUtc": first_start.isoformat() if first_start else None,
        "lockAtUtc": lock_at.isoformat() if lock_at else None,
        "locked": locked,
        "lockStatus": "LOCKED" if locked else "OPEN_PRE_LOCK",
        "source": source,
        "minutesUntilFirstGameStart": round((first_start - now).total_seconds() / 60.0, 2) if first_start else None,
        "minutesUntilSlateLock": round((lock_at - now).total_seconds() / 60.0, 2) if lock_at else None,
        "totalPullCountAvailable": len(pulls),
        "scoringPullCount": len(scoring),
        "latestAvailablePullAt": latest.get("pulled_at"),
        "latestScoringPullAt": latest_scoring.get("pulled_at"),
        "rules": [
            "All predictions for the slate lock together 45 minutes before the first game begins.",
            "After lock, later pulls may continue for audit but cannot change picks.",
            "Locked scoring uses only pulls captured at or before the lock timestamp.",
        ],
    }
    state["_scoring_pulls"] = scoring
    return state


def _enhance(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import mlb_winner_stack_v2
        return mlb_winner_stack_v2.enhance_result(result)
    except Exception as exc:
        if isinstance(result, dict):
            result["winnerStackV2"] = {"applied": False, "error": str(exc)}
        return result


def _optimize_locked_row(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import mlb_fundamentals_optimizer_patch
        return mlb_fundamentals_optimizer_patch.optimize_with_fundamentals(row)
    except Exception as exc:
        out = dict(row or {})
        out["fundamentalsCalibrationNoPick"] = {"applied": False, "error": str(exc)}
        return out


def _slate_from_call(args: Tuple[Any, ...], kwargs: Dict[str, Any], module: Any) -> str:
    if args and args[0]:
        return str(args[0])
    if kwargs.get("slate_date"):
        return str(kwargs["slate_date"])
    try:
        return module._today_et()
    except Exception:
        return datetime.now(SLATE_TZ).date().isoformat()


def _limit(kwargs: Dict[str, Any]) -> int:
    try:
        return int(kwargs.get("limit") or 500)
    except Exception:
        return 500


def _attach_lock(result: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(result)
    public = {k: v for k, v in state.items() if not k.startswith("_")}
    out["slatePredictionLock"] = public
    out["totalPullCountAvailable"] = public.get("totalPullCountAvailable")
    out["scoringPullCount"] = public.get("scoringPullCount")
    out["latestScoringPullAt"] = public.get("latestScoringPullAt")
    for row in out.get("predictions") or []:
        if isinstance(row, dict):
            row["slatePredictionLock"] = public
            if public.get("locked"):
                row["lockedPrediction"] = True
                row["lockedAtUtc"] = public.get("lockAtUtc")
                row["predictionSourcePullAt"] = public.get("latestScoringPullAt")
                row["tags"] = sorted(set((row.get("tags") or []) + ["SLATE_LOCKED", "SLATE_WIDE_45_MIN_LOCK_POLICY"]))
    return out


def _locked_result(module: Any, result: Dict[str, Any], args: Tuple[Any, ...], kwargs: Dict[str, Any], store: bool) -> Dict[str, Any]:
    slate = str((result or {}).get("slate_date") or _slate_from_call(args, kwargs, module))
    pulls = module.history.query_pulls("mlb", slate, _limit(kwargs))
    if not pulls:
        return result
    state = _lock_state(pulls, slate)
    if not state.get("locked"):
        return _attach_lock(result, state)
    scoring = state.get("_scoring_pulls") or pulls
    public = {k: v for k, v in state.items() if not k.startswith("_")}
    games = _latest_games(scoring, slate)
    predictions = []
    stored = []
    for game in games:
        row = module._prediction_for_game(scoring, game, slate)
        if not row:
            continue
        row = _optimize_locked_row(row)
        row["slatePredictionLock"] = public
        row["lockedPrediction"] = True
        row["lockedAtUtc"] = public.get("lockAtUtc")
        row["predictionSourcePullAt"] = public.get("latestScoringPullAt")
        row["tags"] = sorted(set((row.get("tags") or []) + ["SLATE_LOCKED", "SLATE_WIDE_45_MIN_LOCK_POLICY"]))
        if store and hasattr(module, "_store_prediction"):
            row["stored"] = module._store_prediction(row)
            stored.append(row.get("stored"))
        predictions.append(row)
    predictions.sort(key=lambda r: (float(r.get("actionablePick") is True), float(r.get("score") or 0), float(r.get("winProbability") or 0)), reverse=True)
    for i, row in enumerate(predictions, 1):
        row["rank"] = i
    latest = pulls[-1]
    latest_scoring = scoring[-1] if scoring else latest
    out = dict(result)
    out.update({
        "ok": True,
        "sport": "mlb",
        "slate_date": slate,
        "pullCount": len(pulls),
        "totalPullCountAvailable": len(pulls),
        "scoringPullCount": len(scoring),
        "latestPullAt": latest.get("pulled_at"),
        "latestScoringPullAt": latest_scoring.get("pulled_at"),
        "gameCount": len(games),
        "count": len(predictions),
        "allGamesPredicted": len(predictions) == len(games),
        "stored": store,
        "storedCount": len([s for s in stored if s and s.get("ok")]),
        "actionablePickCount": len([r for r in predictions if r.get("actionablePick")]),
        "noPickCount": len([r for r in predictions if not r.get("actionablePick")]),
        "slatePredictionLock": public,
        "predictions": predictions,
    })
    out = _enhance(out)
    out["slatePredictionLock"] = public
    for row in out.get("predictions") or []:
        if isinstance(row, dict):
            row["slatePredictionLock"] = public
            row["lockedPrediction"] = True
            row["lockedAtUtc"] = public.get("lockAtUtc")
            row["predictionSourcePullAt"] = public.get("latestScoringPullAt")
    return out


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_SLATE_PREDICTION_LOCK_APPLIED", False):
        return module
    try:
        import mlb_winner_stack_v2
        mlb_winner_stack_v2.apply(module)
    except Exception:
        pass
    original = module.predict_all

    def patched_predict_all(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            return _locked_result(module, result, args, kwargs, bool(kwargs.get("store")))
        except Exception as exc:
            if isinstance(result, dict):
                result["slatePredictionLock"] = {"applied": False, "policyVersion": POLICY_VERSION, "error": str(exc)}
            return result

    module.predict_all = patched_predict_all
    module._INQSI_MLB_SLATE_PREDICTION_LOCK_APPLIED = True
    return module
