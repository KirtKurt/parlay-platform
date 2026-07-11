from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "MLB-SLATE-COVERAGE-v2-doubleheader-safe-complete-manifest"


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


def _norm_team(value: Any) -> str:
    return " ".join(str(value or "").lower().strip().split())


def _start_key(game: Dict[str, Any]) -> str:
    dt = _parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.isoformat() if dt else str(game.get("commence_time") or game.get("commenceTime") or "unknown")


def game_identity(game: Dict[str, Any]) -> str:
    """Return a stable game identity that never collapses doubleheaders."""
    provider_id = game.get("game_id") or game.get("gameId") or game.get("id") or game.get("gameIdentity")
    if provider_id:
        return f"provider:{provider_id}"
    game_key = str(game.get("game_key") or game.get("gameKey") or "").strip()
    start = _start_key(game)
    if game_key:
        return f"key:{game_key}|start:{start}"
    away = _norm_team(game.get("away_team") or game.get("awayTeam"))
    home = _norm_team(game.get("home_team") or game.get("homeTeam"))
    return f"teams:{away}|{home}|start:{start}"


def _latest_games(lock_module: Any, pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
    by_identity: Dict[str, Tuple[datetime, Dict[str, Any]]] = {}
    for pull in pulls or []:
        pulled_at = lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc)
        for game in pull.get("games") or []:
            if lock_module._game_day(game) != slate:
                continue
            identity = game_identity(game)
            current = by_identity.get(identity)
            if current is None or pulled_at >= current[0]:
                by_identity[identity] = (pulled_at, game)
    return sorted((item[1] for item in by_identity.values()), key=lock_module._game_sort)


def _coverage(games: List[Dict[str, Any]], predictions: List[Dict[str, Any]], stored: List[Dict[str, Any]], store_requested: bool) -> Dict[str, Any]:
    expected = {game_identity(game): game for game in games}
    produced = {game_identity(row): row for row in predictions if row.get("predictedWinner")}
    missing = sorted(set(expected) - set(produced))
    extra = sorted(set(produced) - set(expected))
    stored_ok = len([row for row in stored if isinstance(row, dict) and row.get("ok")])
    matchup_counts: Dict[str, int] = {}
    for game in games:
        matchup = f"{_norm_team(game.get('away_team') or game.get('awayTeam'))}|{_norm_team(game.get('home_team') or game.get('homeTeam'))}"
        matchup_counts[matchup] = matchup_counts.get(matchup, 0) + 1
    doubleheaders = sorted(key for key, count in matchup_counts.items() if count > 1)
    complete = not missing and not extra and len(produced) == len(expected)
    if store_requested:
        complete = complete and stored_ok == len(produced)
    return {
        "applied": True,
        "version": VERSION,
        "strictCoverageRequired": True,
        "doubleheaderSafeIdentity": True,
        "manifestGameCount": len(expected),
        "predictionGameCount": len(produced),
        "storedPredictionCount": stored_ok,
        "storeRequested": bool(store_requested),
        "coverageRatio": round(len(produced) / len(expected), 4) if expected else None,
        "coverageComplete": complete,
        "operationalStatus": "COMPLETE" if complete else "INCOMPLETE_BLOCKED",
        "missingGameIdentities": missing,
        "extraGameIdentities": extra,
        "manifestGameIdentities": sorted(expected),
        "predictionGameIdentities": sorted(produced),
        "doubleheaderMatchups": doubleheaders,
        "publicAccuracyEligible": complete,
        "rules": [
            "Provider game id is the primary identity.",
            "When provider id is unavailable, game key plus commence time is required.",
            "Same-team doubleheaders must remain separate lock-manifest rows.",
            "A locked slate is incomplete when any manifest game lacks a stored winner prediction.",
        ],
    }


def apply(lock_module: Any):
    if getattr(lock_module, "_INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED", False):
        return lock_module

    lock_module._game_key = game_identity

    def latest_games(pulls: List[Dict[str, Any]], slate: str) -> List[Dict[str, Any]]:
        return _latest_games(lock_module, pulls, slate)

    lock_module._latest_games = latest_games
    original_lock_state = lock_module._lock_state

    def lock_state(pulls: List[Dict[str, Any]], slate: str) -> Dict[str, Any]:
        state = original_lock_state(pulls, slate)
        scoring = state.get("_scoring_pulls") or pulls
        manifest = latest_games(scoring, slate)
        public = {
            "manifestVersion": VERSION,
            "manifestGameCount": len(manifest),
            "manifestGameIdentities": [game_identity(game) for game in manifest],
            "doubleheaderSafeIdentity": True,
        }
        state.update(public)
        return state

    lock_module._lock_state = lock_state

    def locked_result(module: Any, result: Dict[str, Any], args: Tuple[Any, ...], kwargs: Dict[str, Any], store: bool) -> Dict[str, Any]:
        slate = str((result or {}).get("slate_date") or lock_module._slate_from_call(args, kwargs, module))
        pulls = module.history.query_pulls("mlb", slate, lock_module._limit(kwargs))
        if not pulls:
            out = dict(result or {})
            out["slateCoverage"] = {
                "applied": True,
                "version": VERSION,
                "coverageComplete": False,
                "operationalStatus": "NO_PULL_HISTORY",
                "publicAccuracyEligible": False,
            }
            return out

        pulls = sorted(pulls, key=lambda pull: lock_module._pull_dt(pull) or datetime.min.replace(tzinfo=timezone.utc))
        state = lock_module._lock_state(pulls, slate)
        if not state.get("locked"):
            out = lock_module._attach_lock(result, state)
            manifest = latest_games(pulls, slate)
            out["slateCoverage"] = _coverage(manifest, out.get("predictions") or [], [], False)
            out["slateCoverage"]["operationalStatus"] = "OPEN_PRE_LOCK"
            out["slateCoverage"]["publicAccuracyEligible"] = False
            return out

        scoring = state.get("_scoring_pulls") or pulls
        public = {key: value for key, value in state.items() if not key.startswith("_")}
        games = latest_games(scoring, slate)
        predictions: List[Dict[str, Any]] = []
        stored: List[Dict[str, Any]] = []
        generation_errors: List[Dict[str, Any]] = []

        for game in games:
            try:
                row = module._prediction_for_game(scoring, game, slate)
            except Exception as exc:
                row = None
                generation_errors.append({"gameIdentity": game_identity(game), "error": str(exc)})
            if not row:
                generation_errors.append({"gameIdentity": game_identity(game), "error": "prediction_generation_returned_none"})
                continue
            row = lock_module._optimize_locked_row(row)
            row["gameIdentity"] = row.get("gameIdentity") or game_identity(game).replace("provider:", "", 1)
            row["slatePredictionLock"] = public
            row["lockedPrediction"] = True
            row["lockedAtUtc"] = public.get("lockAtUtc")
            row["predictionSourcePullAt"] = public.get("latestScoringPullAt")
            row["slateCoverageVersion"] = VERSION
            row["tags"] = sorted(set((row.get("tags") or []) + ["SLATE_LOCKED", "SLATE_WIDE_45_MIN_LOCK_POLICY", "DOUBLEHEADER_SAFE_GAME_IDENTITY"]))
            if store and hasattr(module, "_store_prediction"):
                try:
                    row["stored"] = module._store_prediction(row)
                except Exception as exc:
                    row["stored"] = {"ok": False, "error": str(exc)}
                stored.append(row.get("stored"))
            predictions.append(row)

        predictions.sort(key=lambda row: (float(row.get("actionablePick") is True), float(row.get("score") or 0), float(row.get("winProbability") or 0)), reverse=True)
        for index, row in enumerate(predictions, 1):
            row["rank"] = index

        latest = pulls[-1]
        latest_scoring = scoring[-1] if scoring else latest
        coverage = _coverage(games, predictions, stored, store)
        coverage["generationErrors"] = generation_errors
        if generation_errors:
            coverage["coverageComplete"] = False
            coverage["operationalStatus"] = "INCOMPLETE_BLOCKED"
            coverage["publicAccuracyEligible"] = False

        public.update({
            "manifestVersion": VERSION,
            "manifestGameCount": coverage.get("manifestGameCount"),
            "manifestGameIdentities": coverage.get("manifestGameIdentities"),
            "coverageComplete": coverage.get("coverageComplete"),
            "coverageStatus": coverage.get("operationalStatus"),
            "doubleheaderSafeIdentity": True,
        })

        out = dict(result or {})
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
            "allGamesPredicted": bool(coverage.get("coverageComplete")),
            "stored": store,
            "storedCount": coverage.get("storedPredictionCount"),
            "actionablePickCount": len([row for row in predictions if row.get("actionablePick") is True]),
            "noPickCount": len([row for row in predictions if row.get("actionablePick") is not True]),
            "slatePredictionLock": public,
            "slateCoverage": coverage,
            "operationalDefect": not bool(coverage.get("coverageComplete")),
            "predictions": predictions,
        })
        out = lock_module._enhance(out)
        out["slatePredictionLock"] = public
        out["slateCoverage"] = coverage
        out["operationalDefect"] = not bool(coverage.get("coverageComplete"))
        for row in out.get("predictions") or []:
            if isinstance(row, dict):
                row["slatePredictionLock"] = public
                row["lockedPrediction"] = True
                row["lockedAtUtc"] = public.get("lockAtUtc")
                row["predictionSourcePullAt"] = public.get("latestScoringPullAt")
                row["slateCoverageVersion"] = VERSION
        return out

    lock_module._locked_result = locked_result
    lock_module.POLICY_VERSION = "MLB-SLATE-WIDE-PREDICTION-LOCK-v2-45MIN-doubleheader-safe-complete-manifest"
    lock_module._INQSI_MLB_SLATE_COVERAGE_PATCH_APPLIED = True
    return lock_module
