"""Live sportsbook inventory for Inqsi ARB.

Audits the active provider sport catalog across configured regions and returns a
stable, deduplicated sportsbook inventory. Inventory discovery deliberately
unions the canonical core market types (moneyline, spread, total, outright)
independently so a sportsbook is not missed merely because it does not quote
moneyline on a particular sport. This is observability/catalog logic; it does
not alter arb qualification or settlement validation.
"""
from __future__ import annotations

import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from provider import BASE, _get, api_key, list_sports

DEFAULT_AUDIT_REGIONS = "us,us2,uk,eu,au"
DEFAULT_AUDIT_MARKETS = "h2h,spreads,totals,outrights"


def _market_list(markets: str, sport: Mapping[str, Any]) -> List[str]:
    requested: List[str] = []
    seen = set()
    for raw in str(markets or "").split(","):
        key = raw.strip()
        if not key or key in seen:
            continue
        if key == "outrights" and not bool(sport.get("has_outrights")):
            continue
        seen.add(key)
        requested.append(key)
    return requested


def _collect_books(payload: Any, books: Dict[str, Dict[str, str]], event_ids: set[str]) -> None:
    if not isinstance(payload, list):
        return
    for event in payload:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("id") or "").strip()
        if event_id:
            event_ids.add(event_id)
        for bookmaker in event.get("bookmakers") or []:
            if not isinstance(bookmaker, dict):
                continue
            book_key = str(bookmaker.get("key") or "").strip().lower()
            title = str(bookmaker.get("title") or book_key).strip()
            if book_key:
                books[book_key] = {"key": book_key, "title": title}


def _scan_one_sport(sport: Mapping[str, Any], *, regions: str, markets: str) -> Dict[str, Any]:
    key = api_key()
    sport_key = str(sport.get("key") or "").strip()
    if not key or not sport_key:
        return {"ok": False, "sport": sport_key, "error": "MISSING_KEY_OR_SPORT"}

    requested = _market_list(markets, sport)
    if not requested:
        return {"ok": False, "sport": sport_key, "error": "NO_APPLICABLE_AUDIT_MARKETS"}

    books: Dict[str, Dict[str, str]] = {}
    event_ids: set[str] = set()
    supported: List[str] = []
    rejected: List[Dict[str, Any]] = []
    provider_meta: Dict[str, Dict[str, Any]] = {}

    # Probe each core market independently. The provider can reject one market
    # while supporting another for the same sport; a combined request would make
    # that ambiguity look like a total sport failure and undercount sportsbooks.
    for market in requested:
        payload, meta = _get(
            f"{BASE}/sports/{urllib.parse.quote(sport_key, safe='')}/odds/",
            {
                "apiKey": key,
                "regions": regions,
                "markets": market,
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
            timeout=20,
        )
        provider_meta[market] = meta
        if meta.get("ok") and isinstance(payload, list):
            supported.append(market)
            _collect_books(payload, books, event_ids)
        else:
            rejected.append({
                "market": market,
                "error": meta.get("error") or "PROVIDER_SCAN_FAILED",
                "status": meta.get("status"),
            })

    if not supported:
        return {
            "ok": False,
            "sport": sport_key,
            "title": sport.get("title"),
            "markets_requested": requested,
            "markets_supported": [],
            "markets_rejected": rejected,
            "error": "NO_CORE_MARKET_PROBE_SUCCEEDED",
            "provider": provider_meta,
        }

    return {
        "ok": True,
        "sport": sport_key,
        "title": sport.get("title"),
        "markets_requested": requested,
        "markets_supported": supported,
        "markets_rejected": rejected,
        "n_events": len(event_ids),
        "sportsbooks": sorted(books.values(), key=lambda row: (row["title"].lower(), row["key"])),
        "provider": provider_meta,
    }


def audit_sportsbooks(
    *,
    regions: Optional[str] = None,
    markets: Optional[str] = None,
    workers: Optional[int] = None,
    all_sports: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scan every active provider sport and deduplicate bookmaker key/title.

    The returned inventory is exact for the completed audit definition: every
    sport in the provider catalog, requested regions, and the union of successful
    requested core-market probes. `complete` is false if any sport has no
    successful core-market probe; unsupported individual market types are
    recorded but do not invalidate an otherwise successful sport audit.
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
    rejected_market_probes = 0
    successful_market_probes = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_scan_one_sport, sport, regions=regions, markets=markets) for sport in sports]
        for future in as_completed(futures):
            row = future.result()
            results.append(row)
            rejected_market_probes += len(row.get("markets_rejected") or [])
            successful_market_probes += len(row.get("markets_supported") or [])
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
        "audit_definition": "union_of_successful_core_market_probes_per_active_sport",
        "sport_count": len(sports),
        "sports_scanned": len(results) - len(failures),
        "sports_failed": len(failures),
        "successful_market_probes": successful_market_probes,
        "rejected_market_probes": rejected_market_probes,
        "failures": sorted(failures, key=lambda row: str(row.get("sport") or ""))[:25],
        "provider": sports_meta,
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }
