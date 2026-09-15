"""US state packs for the national ARB information desk.

A pack is not a sportsbook license and not a settlement proof. It records:
- whether the state has legal online sports betting
- which Odds API book keys are known to operate there (footprint hint)
- which books have reviewed settlement rows for that jurisdiction

Jurisdiction-specific house rules are never copied from another state.
DraftKings general sport-rule pages are national (`*`) and apply via fallback.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from rules import lookup, registry_rows

AS_OF = "2026-09-15"
SPORTS = ("baseball", "americanfootball", "basketball", "icehockey", "soccer", "tennis")
FAMILIES = ("winner", "spreads", "totals")

# Online sports betting live as of Sep 2026 reporting. Retail-only/monopoly
# notes live in `notes`, not as fake settlement coverage.
ONLINE_STATES = {
    "ar": "Arkansas", "az": "Arizona", "co": "Colorado", "ct": "Connecticut",
    "dc": "District of Columbia", "de": "Delaware", "fl": "Florida",
    "ia": "Iowa", "il": "Illinois", "in": "Indiana", "ks": "Kansas",
    "ky": "Kentucky", "la": "Louisiana", "ma": "Massachusetts", "md": "Maryland",
    "me": "Maine", "mi": "Michigan", "mo": "Missouri", "nc": "North Carolina",
    "nh": "New Hampshire", "nj": "New Jersey", "nv": "Nevada", "ny": "New York",
    "oh": "Ohio", "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island",
    "tn": "Tennessee", "va": "Virginia", "vt": "Vermont", "wv": "West Virginia",
    "wy": "Wyoming",
}

# Odds API bookmaker keys → states with a known legal online footprint.
# Footprints change; this is availability, not settlement.
BOOK_STATES: Dict[str, Set[str]] = {
    "draftkings": {
        "ar", "az", "co", "ct", "dc", "ia", "il", "in", "ks", "ky", "la", "ma",
        "md", "me", "mi", "mo", "nc", "nh", "nj", "ny", "oh", "or", "pa", "tn",
        "va", "vt", "wv", "wy",
    },
    "fanduel": {
        "az", "ar", "co", "ct", "dc", "ia", "il", "in", "ks", "ky", "la", "ma",
        "md", "mi", "mo", "nc", "nj", "ny", "oh", "pa", "tn", "va", "vt", "wv", "wy",
    },
    "betmgm": {
        "az", "co", "dc", "ia", "il", "in", "ks", "ky", "la", "ma", "md", "mi",
        "mo", "nc", "nj", "oh", "pa", "tn", "va", "wv", "wy",
    },
    "williamhill_us": {
        "az", "co", "dc", "ia", "il", "in", "ks", "ky", "la", "ma", "md", "me",
        "mi", "mo", "nc", "nj", "ny", "oh", "pa", "tn", "va", "wv", "wy",
    },
    "fanatics": {
        "az", "co", "ct", "dc", "ia", "il", "in", "ks", "ky", "la", "ma", "md",
        "mi", "mo", "nc", "nj", "ny", "oh", "pa", "tn", "va", "vt", "wv", "wy",
    },
    "espnbet": {
        "az", "co", "dc", "ia", "il", "in", "ks", "ky", "la", "ma", "md", "mi", "mo",
        "nc", "nj", "ny", "oh", "pa", "tn", "va", "vt", "wv",
    },
    "hardrockbet": {"az", "co", "fl", "il", "in", "mi", "nj", "oh", "tn", "va"},
    "hardrockbet_az": {"az"},
    "hardrockbet_fl": {"fl"},
    "hardrockbet_oh": {"oh"},
    "betrivers": {"az", "co", "de", "ia", "il", "in", "la", "md", "mi", "nj", "ny", "oh", "pa", "va", "wv"},
    "pointsbetus": {"co", "ia", "il", "in", "ks", "la", "nj", "va"},
    "ballybet": {"az", "co", "ia", "in", "ma", "md", "nj", "ny", "oh", "tn", "va"},
    "betparx": {"co", "md", "nj", "oh", "pa", "wv"},
}

NOTES = {
    "fl": "Hard Rock is the online monopoly; other national books are not licensed.",
    "de": "Single-operator online market (BetRivers).",
    "nh": "DraftKings operates under the state lottery contract.",
    "or": "DraftKings operates under the Oregon Lottery contract.",
    "nv": "Retail-heavy market; national apps are not a drop-in online desk.",
}


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def licensed_books(state: str) -> List[str]:
    key = _norm(state)
    if key in {"*", "", "worldwide"}:
        return sorted(BOOK_STATES)
    return sorted(book for book, states in BOOK_STATES.items() if key in states)


def _reviewed_books(state: str) -> List[str]:
    key = _norm(state)
    found: Set[str] = set()
    for book in set(BOOK_STATES) | {row["book"] for row in registry_rows()}:
        for sport in SPORTS:
            for family in FAMILIES:
                rule = lookup(book, sport, family, key)
                if rule is not None and rule.reviewed:
                    found.add(book)
                    break
            if book in found:
                break
    return sorted(found)


def _missing_high_volume(state: str, licensed: Iterable[str], reviewed: Iterable[str]) -> List[str]:
    reviewed_set = set(reviewed)
    priority = (
        "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics",
        "espnbet", "betrivers", "hardrockbet", "pointsbetus", "ballybet", "betparx",
    )
    return [book for book in priority if book in set(licensed) and book not in reviewed_set]


def pack_summary(state: str) -> Dict[str, Any]:
    key = _norm(state)
    if key not in ONLINE_STATES and key not in {"*", "worldwide"}:
        return {
            "ok": False,
            "state": key,
            "error": "UNKNOWN_OR_NO_ONLINE_PACK",
            "as_of": AS_OF,
        }
    licensed = licensed_books(key)
    reviewed = _reviewed_books(key if key not in {"*", "worldwide"} else "*")
    return {
        "ok": True,
        "state": key,
        "name": "Worldwide" if key in {"*", "worldwide"} else ONLINE_STATES.get(key, key),
        "online_legal": key in ONLINE_STATES or key in {"*", "worldwide"},
        "as_of": AS_OF,
        "notes": NOTES.get(key, ""),
        "licensed_books": licensed,
        "reviewed_books": reviewed,
        "missing_high_volume_books": _missing_high_volume(key, licensed, reviewed),
        "n_licensed_books": len(licensed),
        "n_reviewed_books": len(reviewed),
        "policy": "Footprint is availability only. Verified arbs still require reviewed compatible settlement rows for the selected jurisdiction.",
    }


def list_packs() -> List[Dict[str, Any]]:
    rows = [pack_summary("*")]
    rows.extend(pack_summary(code) for code in sorted(ONLINE_STATES))
    return rows


def filter_quotes(quotes: Iterable[Dict[str, Any]], state: str) -> List[Dict[str, Any]]:
    allowed = set(licensed_books(state))
    if not allowed:
        return list(quotes or [])
    return [dict(q) for q in quotes or [] if _norm(str(q.get("book") or "")) in allowed]
