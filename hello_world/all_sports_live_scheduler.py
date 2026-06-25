import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import inqsi_pull_history as history
import mlb_b10_engine
import odds_live_ingestion

try:
    import sport_key_patch
    sport_key_patch.apply(odds_live_ingestion)
except Exception:
    pass

try:
    import auto_build_runner
except Exception:
    auto_build_runner = None

DEFAULT_SPORTS = ["mlb", "wnba", "nfl", "cfb", "nba", "ncaam", "nhl", "soccer", "tennis"]


def _sports_from_event(event: Dict[str, Any]) -> List[str]:
    raw = event.get("sports") or event.get("sport") or ",".join(DEFAULT_SPORTS)
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _strict_build(sport: str) -> Dict[str, Any]:
    if sport == "mlb":
        return mlb_b10_engine.build(_today())
    if auto_build_runner is None:
        return {"ok": False, "sport": sport, "buildStatus": "NO_BUILD", "reason": "AUTO_BUILD_RUNNER_UNAVAILABLE"}
    return auto_build_runner.strict_result(sport)


def _store_build(result: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return history.store_parlay_build(result, mode="aws_all_sports_1am_15min_live_scheduler")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    event = event or {}
    sports = _sports_from_event(event)
    run_type = event.get("run") or "all_sports_hot_pull"
    started_at = datetime.now(timezone.utc).isoformat()

    pull_report = odds_live_ingestion.pull_many(sports)
    results = []
    for sport in sports:
        build = _strict_build(odds_live_ingestion.sport_key(sport))
        build["stored"] = _store_build(build)
        results.append(build)

    built = [r.get("sport") for r in results if r.get("buildStatus") == "BUILT"]
    body = {
        "ok": True,
        "run": run_type,
        "startedAt": started_at,
        "finishedAt": datetime.now(timezone.utc).isoformat(),
        "policy": "All configured sports with active games or matches use timestamped HOT pulls starting at 1:00 AM ET, then every 15 minutes, and attempt strict 3-leg build after 12 snapshots before the 2-hour event deadline.",
        "sports": sports,
        "pullReport": pull_report,
        "builtSports": built,
        "buildResults": results,
    }
    return {"statusCode": 200, "body": json.dumps(body, default=str)}
