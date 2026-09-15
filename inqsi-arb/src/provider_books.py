"""Odds API sportsbook inventory for the national ARB information desk.

This is the product catalog: every provider book the desk can see. It is not a
settlement proof and not a license. Math scans still use every book the Odds API
returns in the requested regions. Verified remains a per-book house-rule overlay.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from rules import lookup
from state_packs import BOOK_STATES, SPORTS, FAMILIES

AS_OF = "2026-09-15"

# Canonical Odds API keys as of 2026-09-14 bookmaker list.
# https://the-odds-api.com/sports-odds-data/bookmaker-apis.html
US_SPORTSBOOKS = {
    "betmgm": {"title": "BetMGM", "regions": ("us",), "kind": "us_licensed"},
    "betrivers": {"title": "BetRivers", "regions": ("us",), "kind": "us_licensed"},
    "williamhill_us": {"title": "Caesars", "regions": ("us",), "kind": "us_licensed"},
    "draftkings": {"title": "DraftKings", "regions": ("us",), "kind": "us_licensed"},
    "fanatics": {"title": "Fanatics", "regions": ("us",), "kind": "us_licensed"},
    "fanduel": {"title": "FanDuel", "regions": ("us",), "kind": "us_licensed"},
    "ballybet": {"title": "Bally Bet", "regions": ("us2",), "kind": "us_licensed"},
    "betparx": {"title": "betPARX", "regions": ("us2",), "kind": "us_licensed"},
    "espnbet": {"title": "theScore Bet", "regions": ("us2",), "kind": "us_licensed"},
    "hardrockbet": {"title": "Hard Rock Bet", "regions": ("us2",), "kind": "us_licensed"},
    "hardrockbet_az": {"title": "Hard Rock Bet (AZ)", "regions": ("us2",), "kind": "us_licensed"},
    "hardrockbet_fl": {"title": "Hard Rock Bet (FL)", "regions": ("us2",), "kind": "us_licensed"},
    "hardrockbet_oh": {"title": "Hard Rock Bet (OH)", "regions": ("us2",), "kind": "us_licensed"},
    "pointsbetus": {"title": "PointsBet (US)", "regions": ("us2",), "kind": "us_licensed"},
    "betanysports": {"title": "BetAnything", "regions": ("us2",), "kind": "us_licensed"},
    "courtside": {"title": "Courtside", "regions": ("us2",), "kind": "us_licensed"},
    "rebet": {"title": "ReBet", "regions": ("us2",), "kind": "us_licensed"},
}

OFFSHORE = {
    "betonlineag": {"title": "BetOnline.ag", "regions": ("us", "eu"), "kind": "offshore"},
    "betus": {"title": "BetUS", "regions": ("us",), "kind": "offshore"},
    "bovada": {"title": "Bovada", "regions": ("us",), "kind": "offshore"},
    "lowvig": {"title": "LowVig.ag", "regions": ("us",), "kind": "offshore"},
    "mybookieag": {"title": "MyBookie.ag", "regions": ("us", "eu"), "kind": "offshore"},
}

DFS = {
    "dabble_us_dfs": {"title": "Dabble", "regions": ("us_dfs",), "kind": "dfs"},
    "pick6": {"title": "DraftKings Pick6", "regions": ("us_dfs",), "kind": "dfs"},
    "prizepicks": {"title": "PrizePicks", "regions": ("us_dfs",), "kind": "dfs"},
    "underdog": {"title": "Underdog Fantasy", "regions": ("us_dfs",), "kind": "dfs"},
    "fliff": {"title": "Fliff", "regions": ("us2",), "kind": "dfs"},
}

EXCHANGES = {
    "betopenly": {"title": "BetOpenly", "regions": ("us_ex",), "kind": "exchange"},
    "kalshi": {"title": "Kalshi", "regions": ("us_ex",), "kind": "exchange"},
    "novig": {"title": "Novig", "regions": ("us_ex",), "kind": "exchange"},
    "polymarket": {"title": "Polymarket", "regions": ("us_ex",), "kind": "exchange"},
    "prophetx": {"title": "ProphetX", "regions": ("us_ex",), "kind": "exchange"},
}

INTERNATIONAL = {
    "pinnacle": {"title": "Pinnacle", "regions": ("eu",), "kind": "international"},
    "betfair_ex_uk": {"title": "Betfair Exchange", "regions": ("uk",), "kind": "exchange"},
    "betfair_ex_eu": {"title": "Betfair Exchange", "regions": ("eu",), "kind": "exchange"},
    "betfair_sb_uk": {"title": "Betfair Sportsbook", "regions": ("uk",), "kind": "international"},
    "williamhill": {"title": "William Hill (UK)", "regions": ("uk", "eu"), "kind": "international"},
    "unibet_uk": {"title": "Unibet", "regions": ("uk",), "kind": "international"},
    "matchbook": {"title": "Matchbook", "regions": ("uk", "eu"), "kind": "exchange"},
    "smarkets": {"title": "Smarkets", "regions": ("uk",), "kind": "exchange"},
    "paddypower": {"title": "Paddy Power", "regions": ("uk",), "kind": "international"},
    "skybet": {"title": "Sky Bet", "regions": ("uk",), "kind": "international"},
    "betvictor": {"title": "Bet Victor", "regions": ("uk", "eu"), "kind": "international"},
    "onexbet": {"title": "1xBet", "regions": ("eu",), "kind": "international"},
}

CATALOG: Dict[str, Dict[str, Any]] = {}
for _group in (US_SPORTSBOOKS, OFFSHORE, DFS, EXCHANGES, INTERNATIONAL):
    CATALOG.update(_group)


def _reviewed_families(book: str, jurisdiction: str = "*") -> List[str]:
    found: Set[str] = set()
    for sport in SPORTS:
        for family in FAMILIES:
            rule = lookup(book, sport, family, jurisdiction)
            if rule is not None and rule.reviewed:
                found.add(f"{sport}:{family}")
    return sorted(found)


def book_row(key: str) -> Dict[str, Any]:
    meta = CATALOG.get(key) or {"title": key, "regions": (), "kind": "unknown"}
    licensed = sorted(BOOK_STATES.get(key, set()))
    return {
        "key": key,
        "title": meta.get("title") or key,
        "kind": meta.get("kind") or "unknown",
        "regions": list(meta.get("regions") or []),
        "licensed_states": licensed,
        "n_licensed_states": len(licensed),
        "reviewed_families": _reviewed_families(key),
        "settlement_reviewed": bool(_reviewed_families(key)),
        "on_math_board": True,
    }


def list_books(*, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    keys = sorted(CATALOG)
    if kind:
        wanted = str(kind).strip().lower()
        keys = [key for key in keys if (CATALOG[key].get("kind") or "") == wanted]
    return [book_row(key) for key in keys]


def catalog_summary() -> Dict[str, Any]:
    rows = list_books()
    by_kind: Dict[str, int] = {}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    return {
        "ok": True,
        "as_of": AS_OF,
        "count": len(rows),
        "n_us_licensed": by_kind.get("us_licensed", 0),
        "n_offshore": by_kind.get("offshore", 0),
        "n_dfs": by_kind.get("dfs", 0),
        "n_exchange": by_kind.get("exchange", 0),
        "n_international": by_kind.get("international", 0),
        "n_settlement_reviewed": sum(1 for row in rows if row["settlement_reviewed"]),
        "by_kind": by_kind,
        "books": rows,
        "places_bets": False,
        "policy": (
            "Every provider-returned sportsbook is on the mathematical board. "
            "State packs are license footprints. Verified is a house-rule overlay "
            "and is never inferred across unread books or states."
        ),
    }
