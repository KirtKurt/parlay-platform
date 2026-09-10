from __future__ import annotations

from persist import save_ratings
from ratings import replay
from sackmann import iter_years


def bootstrap_tour(tour: str, start: int = 2014, end: int = 2026) -> dict:
    rows = list(iter_years(tour, start, end))
    rows.sort(key=lambda r: (str(r.get("tourney_date") or ""), str(r.get("match_num") or "")))
    store = replay(rows)
    save_ratings(tour, store)
    return {
        "stack": "tennis-alpha",
        "tour": tour,
        "matches": store.matches,
        "players": len(store.elo.overall),
        "start": start,
        "end": end,
    }
