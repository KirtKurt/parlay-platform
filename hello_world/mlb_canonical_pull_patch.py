"""Ensure the AWS MLB scheduler writes canonical 15-minute pull history.

The older mlb_manual_pull scheduler stores date-isolated HOT snapshots under
SPORT#mlb... keys. The MLB-B1.0 proof engine reads canonical PULLS#mlb#DATE
records through inqsi_pull_history.query_pulls. This patch bridges the two so
AWS EventBridge scheduled pulls are counted by the production proof engine.
"""

from __future__ import annotations

from typing import Any, Dict, List

import inqsi_pull_history as history

try:
    import slate_date_patch
    slate_date_patch.apply_to_history(history)
except Exception:
    pass

try:
    import pull_dedupe_guard
    pull_dedupe_guard.apply(history)
except Exception:
    pass


CANONICAL_SOURCE = "aws_eventbridge_mlb_hot_scheduler"
PROVIDER_SPORT_KEY = "baseball_mlb"


def _canonical_games(compact: Dict[str, Any]) -> List[Dict[str, Any]]:
    games: List[Dict[str, Any]] = []
    for game in compact.get("games") or []:
        if not isinstance(game, dict):
            continue
        home = game.get("home_team")
        away = game.get("away_team")
        books = game.get("books") or {}
        if not home or not away or not books:
            continue
        game_id = str(game.get("game_id") or game.get("id") or game.get("game_key") or "")
        games.append({
            "game_id": game_id,
            "id": game_id,
            "game_key": game.get("game_key") or game_id,
            "home_team": home,
            "away_team": away,
            "commence_time": game.get("commence_time"),
            "provider_sport_key": game.get("provider_sport_key") or PROVIDER_SPORT_KEY,
            "books": books,
        })
    return games


def _store_canonical_pull(*, compact: Dict[str, Any], slate_date: str, asof: str, run: str) -> Dict[str, Any]:
    games = _canonical_games(compact)
    if not games:
        return {
            "ok": True,
            "canonical": True,
            "stored": False,
            "reason": "NO_CANONICAL_GAMES_TO_STORE",
            "game_count": 0,
        }
    payload = {
        "sport": "mlb",
        "pulled_at": asof,
        "slate_date": slate_date,
        "source": CANONICAL_SOURCE,
        "interval_minutes": 15,
        "provider_sport_key": PROVIDER_SPORT_KEY,
        "run": run,
        "games": games,
        "meta": {
            "canonicalPullHistory": True,
            "sourceScheduler": "MLBAuditedPullFunction",
            "legacySnapshotBridge": True,
            "run": run,
        },
    }
    result = history.store_pull(payload)
    return {
        "ok": bool(result.get("ok")),
        "canonical": True,
        "stored": bool((result.get("stored") or {}).get("sk")),
        "deduped": bool(result.get("deduped")),
        "pk": (result.get("stored") or {}).get("pk"),
        "sk": (result.get("stored") or {}).get("sk"),
        "game_count": len(games),
        "source": CANONICAL_SOURCE,
        "error": result.get("error"),
    }


def apply(mlb_manual_pull_module: Any) -> None:
    if mlb_manual_pull_module is None or getattr(mlb_manual_pull_module, "_inqsi_canonical_pull_patch_installed", False):
        return

    original_store_snapshot_item = mlb_manual_pull_module._store_snapshot_item

    def _store_snapshot_item(*, t: str, slate_date: str, game_date: str, asof: str, run: str, compact: Dict[str, Any], date_isolated: bool, pk: str):
        stored = original_store_snapshot_item(
            t=t,
            slate_date=slate_date,
            game_date=game_date,
            asof=asof,
            run=run,
            compact=compact,
            date_isolated=date_isolated,
            pk=pk,
        )
        # Store exactly one canonical PULLS record per HOT pull. The date-isolated
        # snapshot writes still happen below for legacy audit endpoints.
        if str(t).upper() == "HOT" and not date_isolated:
            try:
                stored["canonicalPullHistory"] = _store_canonical_pull(
                    compact=compact,
                    slate_date=slate_date,
                    asof=asof,
                    run=run,
                )
            except Exception as exc:
                stored["canonicalPullHistory"] = {
                    "ok": False,
                    "canonical": True,
                    "stored": False,
                    "error": str(exc),
                }
        return stored

    mlb_manual_pull_module._store_snapshot_item = _store_snapshot_item
    mlb_manual_pull_module._inqsi_canonical_pull_patch_installed = True
