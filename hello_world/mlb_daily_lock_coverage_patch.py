from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from mlb_slate_coverage_patch import VERSION as COVERAGE_VERSION, game_identity

VERSION = "INQSI-MLB-DAILY-LOCK-v3-complete-slate-doubleheader-safe"


def _latest_games(module: Any, slate_date: str, pulls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_identity: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
    for pull in pulls or []:
        pulled_at = module._parse_dt(pull.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc)
        for game in pull.get("games") or []:
            if module._game_date_et(game) != slate_date:
                continue
            identity = game_identity(game)
            current = by_identity.get(identity)
            if current is None or pulled_at >= current[0]:
                by_identity[identity] = (pulled_at, game)
    return sorted(
        (item[1] for item in by_identity.values()),
        key=lambda game: module._parse_dt(game.get("commence_time") or game.get("commenceTime")) or datetime.max.replace(tzinfo=timezone.utc),
    )


def _coverage(module: Any, games: List[Dict[str, Any]], payload: Dict[str, Any], predictions: List[Dict[str, Any]]) -> Dict[str, Any]:
    expected = {game_identity(game) for game in games}
    produced = {game_identity(row) for row in predictions if row.get("predictedWinner")}
    missing = sorted(expected - produced)
    extra = sorted(produced - expected)
    engine_coverage = dict(payload.get("slateCoverage") or {})
    stored_count = int(payload.get("storedCount") or 0)
    complete = bool(
        expected
        and not missing
        and not extra
        and len(produced) == len(expected)
        and payload.get("allGamesPredicted") is True
        and engine_coverage.get("coverageComplete") is True
        and stored_count == len(produced)
    )
    return {
        "applied": True,
        "version": VERSION,
        "coverageVersion": engine_coverage.get("version") or COVERAGE_VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "manifestGameCount": len(expected),
        "predictionCount": len(produced),
        "storedPredictionCount": stored_count,
        "missingGameIdentities": missing,
        "extraGameIdentities": extra,
        "manifestGameIdentities": sorted(expected),
        "predictionGameIdentities": sorted(produced),
        "coverageComplete": complete,
        "operationalStatus": "COMPLETE" if complete else "INCOMPLETE_NOT_LOCKED",
        "engineCoverage": engine_coverage,
    }


def apply(module: Any):
    if getattr(module, "_INQSI_MLB_DAILY_LOCK_COVERAGE_PATCH_APPLIED", False):
        return module

    module.MODEL_VERSION = VERSION

    def latest_games_for_date(slate_date: str, pulls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return _latest_games(module, slate_date, pulls)

    module._latest_games_for_date = latest_games_for_date
    original_lock_response = module._lock_response

    def lock_response(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        response = original_lock_response(item)
        if not response or not item:
            return response
        data = item.get("data") or {}
        response.update({
            "manifestVersion": item.get("manifest_version"),
            "manifestGameCount": item.get("manifest_game_count"),
            "manifestGameIdentities": data.get("manifestGameIdentities") or [],
            "coverageComplete": item.get("coverage_complete"),
            "coverageStatus": item.get("coverage_status"),
            "doubleheaderSafeIdentity": item.get("doubleheader_safe_identity"),
            "slateCoverage": data.get("slateCoverage") or {},
            "publicAccuracyEligible": bool(item.get("coverage_complete")),
        })
        return response

    module._lock_response = lock_response

    def run_lock(slate_date: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        slate = slate_date or module._today_et()
        if module.TABLE is None:
            return {"ok": False, "sport": "mlb", "error": "SNAPSHOTS_TABLE not configured"}

        existing = module._lock_response(module._get_lock_item(slate))
        if existing:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": existing}

        pulls = module._pulls_for_date(slate)
        if not pulls:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_STORED_ODDS_API_PULL_HISTORY"}
        pulls = sorted(pulls, key=lambda pull: module._parse_dt(pull.get("pulled_at")) or datetime.min.replace(tzinfo=timezone.utc))

        games = latest_games_for_date(slate, pulls)
        first = module._first_start_et(games)
        if not games or first is None:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "NO_MLB_GAMES_FOR_SLATE_DATE", "pullCount": len(pulls)}

        lock_time = first - module.timedelta(minutes=module.LOCK_MINUTES)
        now_utc = module._now_utc()
        now_et = now_utc.astimezone(module.EASTERN)
        if now_et >= first:
            return {
                "ok": False,
                "sport": "mlb",
                "modelVersion": VERSION,
                "slateDateEt": slate,
                "locked": False,
                "reason": "MISSED_FULL_SLATE_LOCK_WINDOW_NOT_BACKFILLED",
                "firstGameStartEt": first.isoformat(),
                "lockTimeEt": lock_time.isoformat(),
                "nowEt": now_et.isoformat(),
                "publicAccuracyEligible": False,
            }
        if now_et < lock_time and not force:
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "skipped": True, "reason": "WAITING_FOR_T_MINUS_LOCK_WINDOW", "nowEt": now_et.isoformat(), "firstGameStartEt": first.isoformat(), "lockTimeEt": lock_time.isoformat(), "minutesUntilLock": round((lock_time - now_et).total_seconds() / 60.0, 2)}

        latest_age = module._latest_pull_age_minutes(pulls, now_utc)
        if latest_age is None or latest_age > module.MAX_LATEST_PULL_AGE_MINUTES:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "STALE_OR_UNREADABLE_LATEST_PULL_NOT_LOCKED", "latestPullAgeMinutes": latest_age, "maxLatestPullAgeMinutes": module.MAX_LATEST_PULL_AGE_MINUTES}

        depths = module._pull_depths(pulls, games)
        min_depth = min(depths.values()) if depths else 0
        if min_depth < module.MIN_PULLS_PER_GAME_FOR_LOCK and not force:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "INSUFFICIENT_PULL_DEPTH_NOT_LOCKED", "minObservedPullDepth": min_depth, "minPullsPerGameForLock": module.MIN_PULLS_PER_GAME_FOR_LOCK, "gameDepths": depths}

        prediction_payload = module.mlb_game_winner_engine.predict_all(slate, store=True, limit=500)
        predictions = prediction_payload.get("predictions") or []
        coverage = _coverage(module, games, prediction_payload, predictions)
        if not predictions:
            return {"ok": False, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": False, "reason": "NO_SINGLE_GAME_ML_PREDICTIONS_AVAILABLE", "slateCoverage": coverage}
        if module.REQUIRE_ALL_GAMES_FOR_LOCK and not coverage.get("coverageComplete"):
            return {
                "ok": False,
                "sport": "mlb",
                "modelVersion": VERSION,
                "slateDateEt": slate,
                "locked": False,
                "reason": "INCOMPLETE_DAILY_CARD_NOT_LOCKED",
                "predictionCount": len(predictions),
                "gameCount": len(games),
                "allGamesPredicted": prediction_payload.get("allGamesPredicted"),
                "slateCoverage": coverage,
                "publicAccuracyEligible": False,
            }

        picks = module._sort_picks([module._compact_pick(row) for row in predictions])
        now_utc = module._now_utc()
        item = module.history.ddb_safe({
            "PK": module._lock_pk(slate),
            "SK": module._lock_sk(),
            "record_type": "mlb_daily_locked_individual_game_moneyline_picks",
            "sport": "mlb",
            "model_version": VERSION,
            "single_game_model": prediction_payload.get("modelVersion"),
            "slate_date": slate,
            "locked": True,
            "locked_at": now_utc.isoformat(),
            "locked_at_et": now_utc.astimezone(module.EASTERN).isoformat(),
            "first_game_start_et": first.isoformat(),
            "first_game_start_utc": first.astimezone(timezone.utc).isoformat(),
            "lock_time_et": lock_time.isoformat(),
            "lock_minutes_before_first_game": module.LOCK_MINUTES,
            "lock_policy": module.LOCK_POLICY,
            "source": "stored_odds_api_pull_history_single_game_ml_complete_slate_manifest",
            "latest_pull_at": pulls[-1].get("pulled_at"),
            "latest_pull_id": pulls[-1].get("pull_id"),
            "latest_pull_age_minutes": latest_age,
            "pull_count": len(pulls),
            "min_pull_depth_for_lock": module.MIN_PULLS_PER_GAME_FOR_LOCK,
            "min_observed_pull_depth": min_depth,
            "game_count": len(games),
            "manifest_version": VERSION,
            "manifest_game_count": coverage.get("manifestGameCount"),
            "prediction_count": len(picks),
            "promoted_count": len([pick for pick in picks if pick.get("promoted")]),
            "all_games_predicted": True,
            "coverage_complete": True,
            "coverage_status": "COMPLETE",
            "doubleheader_safe_identity": True,
            "data": {
                "picks": picks,
                "manifestGameIdentities": coverage.get("manifestGameIdentities") or [],
                "slateCoverage": coverage,
                "predictionSummary": {
                    "engine": prediction_payload.get("engine"),
                    "modelVersion": prediction_payload.get("modelVersion"),
                    "promotedCount": prediction_payload.get("promotedCount"),
                    "storedCount": prediction_payload.get("storedCount"),
                    "allGamesPredicted": True,
                },
            },
            "created_at": now_utc.isoformat(),
        })
        try:
            module.TABLE.put_item(Item=item, ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)")
            return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": False, "lock": module._lock_response(item)}
        except module.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return {"ok": True, "sport": "mlb", "modelVersion": VERSION, "slateDateEt": slate, "locked": True, "alreadyLocked": True, "lock": module._lock_response(module._get_lock_item(slate))}
            raise

    module.run_lock = run_lock
    module._INQSI_MLB_DAILY_LOCK_COVERAGE_PATCH_APPLIED = True
    return module
