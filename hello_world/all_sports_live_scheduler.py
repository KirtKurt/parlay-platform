import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import inqsi_pull_history as history
import mlb_b10_engine
import odds_live_ingestion

try:
    import mlb_game_winner_engine
except Exception:
    mlb_game_winner_engine = None

try:
    import slate_date_patch
    slate_date_patch.apply_to_history(history)
    slate_date_patch.apply_to_odds(odds_live_ingestion)
except Exception:
    pass

try:
    import signal_score_guard
    signal_score_guard.apply(history)
except Exception:
    pass

try:
    import pull_dedupe_guard
    pull_dedupe_guard.apply(history)
except Exception:
    pass

try:
    import pull_report_guard
    pull_report_guard.apply(odds_live_ingestion)
except Exception:
    pass

try:
    import baseline_parlay_builder
except Exception:
    baseline_parlay_builder = None

try:
    import sport_key_patch
    sport_key_patch.apply(odds_live_ingestion)
except Exception:
    pass

try:
    import auto_build_runner
except Exception:
    auto_build_runner = None

SLATE_TZ = ZoneInfo("America/New_York")
DEFAULT_SPORTS = ["mlb", "wnba", "nfl", "cfb", "nba", "ncaam", "nhl", "soccer", "tennis"]


def _sports_from_event(event: Dict[str, Any]) -> List[str]:
    raw = event.get("sports") or event.get("sport") or ",".join(DEFAULT_SPORTS)
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _today() -> str:
    return datetime.now(SLATE_TZ).date().isoformat()


def _baseline(result: Dict[str, Any], sport: str) -> Dict[str, Any]:
    if baseline_parlay_builder is None:
        return result
    try:
        return baseline_parlay_builder.apply_if_needed(result, sport, result.get("slate_date") or _today())
    except Exception as exc:
        result["baselineBuildError"] = str(exc)
        return result


def _strict_build(sport: str) -> Dict[str, Any]:
    if sport == "mlb":
        return _baseline(mlb_b10_engine.build(_today()), sport)
    if auto_build_runner is None:
        return {"ok": False, "sport": sport, "buildStatus": "NO_BUILD", "reason": "AUTO_BUILD_RUNNER_UNAVAILABLE"}
    return _baseline(auto_build_runner.strict_result(sport), sport)


def _store_build(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return history.store_parlay_build(result, mode="aws_all_sports_15min_with_baseline")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _mlb_game_winners() -> Dict[str, Any]:
    if mlb_game_winner_engine is None:
        return {"ok": False, "error": "mlb_game_winner_engine_unavailable"}
    try:
        return mlb_game_winner_engine.predict_all(_today(), store=True, limit=500)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    sports = _sports_from_event(event)
    started_at = datetime.now(timezone.utc).isoformat()
    pull_report = odds_live_ingestion.pull_many(sports)
    mlb_winners = _mlb_game_winners() if "mlb" in [odds_live_ingestion.sport_key(s) for s in sports] else None
    results = []
    for sport in sports:
        build = _strict_build(odds_live_ingestion.sport_key(sport))
        build["stored"] = _store_build(build)
        results.append(build)
    body = {
        "ok": True,
        "run": event.get("run") or "all_sports_hot_pull",
        "startedAt": started_at,
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "slateDateEt": _today(),
        "sports": sports,
        "pullReport": pull_report,
        "mlbGameWinnerPredictions": mlb_winners,
        "builtSports": [r.get("sport") for r in results if r.get("buildStatus") == "BUILT"],
        "buildResults": results,
    }
    return {"statusCode": 200, "body": json.dumps(body, default=str)}
