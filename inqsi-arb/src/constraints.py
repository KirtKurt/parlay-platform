"""Constraint analysis for hypothetical ARB stake plans.

Pure calculation only: it never places transactions or communicates with books.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping


def apply_book_constraints(legs: Iterable[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    feasible = True
    reasons: List[str] = []
    for raw in legs or []:
        leg = dict(raw)
        book = str(leg.get("book") or "").lower()
        required = float(leg.get("stake") or 0.0)
        profile = dict(profiles.get(book) or {})
        balance = profile.get("balance")
        user_limit = profile.get("max_stake")
        quote_limit = leg.get("limit")
        caps = []
        for value in (balance, user_limit, quote_limit):
            try:
                if value is not None and float(value) >= 0:
                    caps.append(float(value))
            except (TypeError, ValueError):
                pass
        cap = min(caps) if caps else None
        leg_ok = cap is None or required <= cap + 1e-9
        if not leg_ok:
            feasible = False
            reasons.append(f"{book}: required {required:.2f} exceeds available cap {cap:.2f}")
        leg["constraint_cap"] = cap
        leg["constraint_status"] = "OK" if leg_ok else "EXCEEDS_CAP"
        rows.append(leg)
    return {"feasible": feasible, "legs": rows, "reasons": reasons}
