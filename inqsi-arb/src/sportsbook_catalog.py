"""Live sportsbook inventory for Inqsi ARB.

Audits the active provider sport catalog across configured regions and returns a
stable, deduplicated sportsbook inventory. This is observability/catalog logic;
it does not alter arb qualification or settlement validation.
"""
from __future__ import annotations

import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from provider import BASE, _get, api_key, list_sports

DEFAULT_AUDIT_REGIONS = "us,us2,uk,eu,au"
DEFAULT_AUDIT_MARKETS = "h2h"


def _scan_one_sport(sport: Mapping[str, Any], *, regions: str, markets: str) -> Dict[str, Any]:
    key = api_key()
    sport_key = str(sport.get("key") or "").strip()
    if not key or not sport_key:
        return {"ok": False, "sport": sport_key, "error": "MISSING_KEY_OR_SPORT"}

    payload, meta = _get(
        f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/odds/",
        {
            "apiKey": key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=20,
    )

    # Some active catalog entries are outright-only. If the canonical event
    # market is rejected and the sport advertises outrights, retry that market.
    used_markets = markets
    if (not meta.get("ok")) and bool(sport.get("has_outrights")) and markets != "outrights":
        payload, meta = _get(
            f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/odds/",
            {
                "apiKey": key,
                "regions": regions,
                "markets": "outrights",
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
            timeout=20,
        )
        used_markets = "outrights"

    if not meta.get("ok") or not isinstance(payload, list):
        return {
            "ok": False,
            "sport": sport_key,
            "title": sport.get("title"),
            "markets": used_markets,
            "error": meta.get("error") or "PROVIDER_SCAN_FAILED",
            "status": meta.get("status"),
        }

    books: Dict[str, Dict[str, str]] = {}
    n_events = 0
    for event in payload:
        if not isinstance(event, dict):
            continue
        n_events += 1
        for bookmaker in event.get("bookmakers") or []:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key") or "").strip().lower()
            title = str(bookmaker.get("title") or book_key).strip()
            if not book_key:
                continue
            books[book_key] = {"key": book_key, "title": title}

    return {
        "ok": True,
        "sport": sport_key,
        "title": sport.get("title"),
        "markets": used_markets,
        "n_events": n_events,
        "sportsbooks": sorted(books.values(), key=lambda row: (row["title"].lower(), row["key"])),
        "provider": meta,
    }


def audit_sportsbooks(
    *,
    regions: Optional[str] = None,
    markets: Optional[str] = None,
    workers: Optional[int] = None,
    all_sports: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan every active provider sport and deduplicate bookmaker key/title.

    The returned count is exact for the completed audit definition: all sports
    returned by the provider catalog, requested regions, and requested canonical
    discovery market(s). `complete` is false if any sport request fails.
    """
    regions = (regions or os.environ.get("ARB_SPORTSBOOK_AUDIT_REGIONS") or DEFAULT_AUDIT_REGIONS).strip()
    markets = (markets or os.environ.get("ARB_SPORTSBOOK_AUDIT_MARKETS") or DEFAULT_AUDIT_MARKETS).strip()
    workers = max(1, min(int(workers or os.environ.get("ARB_SPORTSBOOK_AUDIT_WORKERS", "12")), 24))

    sports, sports_meta = list_sports(all_sports=all_sports)
    if not sports_meta.get("ok"):
        return [], {
            "ok": False,
            "complete": False,
            "error": "SPORT_CATALOG_UNAVAILABLE",
            "provider": sports_meta,
            "regions": regions,
            "markets": markets,
        }

    by_book: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_scan_one_sport, sport, regions=regions, markets=markets) for sport in sports]
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            if not row.get("ok"):
                failures.append(row)
                continue
            for book in row.get("sportsbooks") or []:
                book_key = str(book.get("key") or "").strip().lower()
                if not book_key:
                    continue
                existing = by_book.setdefault(book_key, {
                    "key": book_key,
                    "title": str(book.get("title") or book_key),
                    "sports": [],
                })
                if row.get("sport") not in existing["sports"]:
                    existing["sports"].append(row.get("sport"))

    books = sorted(by_book.values(), key=lambda row: (str(row.get("title") or "").lower(), row["key"]))
    for book in books:
        book["sports"] = sorted(book["sports"])
        book["sport_count"] = len(book["sports"])

    complete = len(failures) == 0
    return books, {
        "ok": complete,
        "complete": complete,
        "regions": regions,
        "markets": markets,
        "sport_count": len(sports),
        "sports_scanned": len(results) - len(failures),
        "sports_failed": len(failures),
        "failures": sorted(failures, key=lambda row: str(row.get("sport") or ""))[:25],
        "provider": sports_meta,
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }
