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
    return (os.environ.get("ARB_COLLECT_REGIONS") or os.environ.get("ARB_US_REGIONS") or "us,us2").strip()


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
    head = put_snapshot(sport, payload.get("events") or [], meta={
        **(payload.get("status") or {}),
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
    results = [collect_sport(sport) for sport in selected]
    put_checkpoint({
        "next_index": (start + limit) % len(keys),
        "last_sports": selected,
        "n_ok": sum(1 for row in results if row.get("ok")),
    })
    return {
        "ok": any(row.get("ok") for row in results),
        "places_bets": False,
        "regions": _regions(),
        "markets": _markets(),
        "n_sports": len(selected),
        "sports": results,
    }


def handler(event: Any, context: Any) -> Dict[str, Any]:
    summary = collect_tick()
    return {"statusCode": 200, "body": json.dumps(summary, default=str)}
