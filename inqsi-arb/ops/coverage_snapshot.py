from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from coverage_store import put_many
from validation import market_family, sport_family

USER_AGENT = "inqsi-arb-coverage-snapshot/1.0"


def fetch_json(url: str, timeout: int = 300) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read() or b"{}")
            if response.status != 200 or not isinstance(body, dict):
                raise RuntimeError(f"HTTP_{response.status}")
            return body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP_{exc.code}: {raw}") from exc


def build_rows(catalog: Dict[str, Any], sportsbooks: Dict[str, Any], rules: Dict[str, Any], *, now_ms: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    sports = catalog.get("sports") or []
    for sport in sports:
        if not isinstance(sport, dict) or not sport.get("key"):
            continue
        active = sport.get("active")
        rows.append({
            "provider": "the_odds_api",
            "sport": sport["key"],
            "competition": sport.get("group") or sport.get("title") or "",
            "provider_support": "SUPPORTED",
            "subscription_access": "ENTITLED",
            "current_offering": "AVAILABLE" if active is not False else "OUT_OF_SEASON",
            "ingestion_health": "HEALTHY",
            "parser_support": "IMPLEMENTED",
            "settlement_verification": "UNKNOWN",
            "freshness": "UNKNOWN",
            "last_successful_fetch_at_ms": now_ms,
            "evidence": {"source": "/v1/arb/catalog?all=true", "active": active},
        })

    for book in sportsbooks.get("sportsbooks") or []:
        if not isinstance(book, dict) or not book.get("key"):
            continue
        book_key = str(book["key"])
        for sport_key in book.get("sports") or []:
            rows.append({
                "provider": "the_odds_api",
                "sport": sport_key,
                "book": book_key,
                "provider_support": "SUPPORTED",
                "subscription_access": "ENTITLED",
                "current_offering": "AVAILABLE",
                "ingestion_health": "HEALTHY",
                "parser_support": "IMPLEMENTED",
                "settlement_verification": "RULES_PENDING",
                "freshness": "UNKNOWN",
                "last_successful_fetch_at_ms": now_ms,
                "evidence": {
                    "source": "/v1/arb/sportsbooks",
                    "book_title": book.get("title"),
                    "audit_regions": sportsbooks.get("regions"),
                    "audit_markets": sportsbooks.get("markets"),
                    "audit_complete": sportsbooks.get("complete"),
                },
            })

    for rule in rules.get("rules") or []:
        if not isinstance(rule, dict) or not rule.get("reviewed"):
            continue
        rows.append({
            "provider": "the_odds_api",
            "sport": rule.get("sport") or "unknown",
            "book": rule.get("book") or "*",
            "jurisdiction": rule.get("jurisdiction") or "*",
            "market_family": rule.get("market_family") or "*",
            "provider_support": "SUPPORTED",
            "subscription_access": "ENTITLED",
            "current_offering": "UNKNOWN",
            "ingestion_health": "HEALTHY",
            "parser_support": "IMPLEMENTED",
            "settlement_verification": "VERIFIED",
            "freshness": "UNKNOWN",
            "last_successful_fetch_at_ms": now_ms,
            "evidence": {
                "source": rule.get("source"),
                "rule_version": rule.get("version"),
                "settlement_profile": rule.get("settlement_profile"),
            },
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--sportsbook-url", required=True)
    parser.add_argument("--output", default="arb-coverage-snapshot.json")
    args = parser.parse_args()

    base = args.api_url.rstrip("/")
    catalog = fetch_json(base + "/v1/arb/catalog?all=true")
    rules = fetch_json(base + "/v1/arb/rules")
    sportsbooks = fetch_json(args.sportsbook_url, timeout=300)
    if not sportsbooks.get("complete") or sportsbooks.get("sports_failed") != 0:
        raise RuntimeError("SPORTSBOOK_AUDIT_INCOMPLETE")

    now_ms = int(time.time() * 1000)
    rows = build_rows(catalog, sportsbooks, rules, now_ms=now_ms)
    persisted = put_many(rows, now_ms=now_ms)
    summary = {
        "ok": True,
        "rows_built": len(rows),
        "rows_persisted": len(persisted),
        "sport_count": catalog.get("n_sports"),
        "sportsbook_count": sportsbooks.get("sportsbook_count"),
        "reviewed_rule_count": rules.get("count"),
        "state_table": os.environ.get("ARB_STATE_TABLE", ""),
        "places_bets": False,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
