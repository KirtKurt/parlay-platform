"""Scheduled quote collector for Inqsi ARB.

Rotates through active provider sports and writes featured-market snapshots.
Never places bets. The Odds API remains the price authority.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from provider import FEATURED, list_sports, scan_sport_payload
from quote_store import get_checkpoint, put_checkpoint, put_snapshot


def _markets() -> List[str]:
    raw = (os.environ.get("ARB_COLLECT_MARKETS") or "h2h,spreads,totals").strip()
    requested = [part.strip() for part in raw.split(",") if part.strip()]
    return [m for m in requested if m in FEATURED] or ["h2h", "spreads", "totals"]


def _regions() -> str:
    return (
        os.environ.get("ARB_COLLECT_REGIONS")
        or os.environ.get("ARB_REGIONS")
        or os.environ.get("ARB_US_REGIONS")
        or "us,us2,us_dfs,us_ex,uk,eu,fr,se,au"
    ).strip()


def _max_sports() -> int:
    try:
        return max(1, min(int(os.environ.get("ARB_COLLECT_MAX_SPORTS", "6")), 20))
    except ValueError:
        return 6


def collect_sport(sport: str) -> Dict[str, Any]:
    markets = _markets()
    regions = _regions()
    payload = scan_sport_payload(
        sport, bankroll=1000.0, markets=markets, regions=regions, max_events=int(os.environ.get("ARB_MAX_EVENT_MARKET_EVENTS", "40")),
    )
    status = dict(payload.get("status") or {})
    if not status.get("ok"):
        # Never replace a known-good HEAD with an unsuccessful provider read.
        return {
            "sport": sport,
            "ok": False,
            "error": status.get("error") or "PROVIDER_FETCH_FAILED",
            "provider": status,
        }
    head = put_snapshot(sport, payload.get("events") or [], meta={
        **status,
        "markets": markets,
        "regions": regions,
    })
    return {"sport": sport, **head}


def collect_tick() -> Dict[str, Any]:
    sports, meta = list_sports(all_sports=False)
    if not meta.get("ok"):
        return {"ok": False, "error": "SPORT_CATALOG_UNAVAILABLE", "provider": meta, "places_bets": False}
    keys = [str(row.get("key") or "") for row in sports if row.get("key") and row.get("active", True)]
    if not keys:
        return {"ok": False, "error": "NO_ACTIVE_SPORTS", "places_bets": False}
    checkpoint = get_checkpoint()
    start = int(checkpoint.get("next_index") or 0) % len(keys)
    limit = min(_max_sports(), len(keys))
    selected = [keys[(start + offset) % len(keys)] for offset in range(limit)]
    results = []
    for sport in selected:
        try:
            results.append(collect_sport(sport))
        except Exception as exc:
            results.append({
                "sport": sport,
                "ok": False,
                "error": type(exc).__name__,
                "detail": str(exc)[:200],
            })
    put_checkpoint({
        "next_index": (start + limit) % len(keys),
        "last_sports": selected,
        "active_sports": keys,
        "n_ok": sum(1 for row in results if row.get("ok")),
    })
    n_ok = sum(1 for row in results if row.get("ok"))
    return {
        "ok": n_ok > 0,
        "all_ok": n_ok == len(results),
        "n_failed": len(results) - n_ok,
        "places_bets": False,
        "regions": _regions(),
        "markets": _markets(),
        "n_sports": len(selected),
        "sports": results,
    }


def handler(event: Any, context: Any) -> Dict[str, Any]:
    summary = collect_tick()
    if not summary.get("ok") or not summary.get("all_ok", True):
        reason = summary.get("error") or (
            "PARTIAL_SPORT_FAILURE" if summary.get("ok") else "ALL_SELECTED_SPORTS_FAILED"
        )
        raise RuntimeError(f"ARB collector tick failed: {reason}")
    return {"statusCode": 200, "body": json.dumps(summary, default=str)}
