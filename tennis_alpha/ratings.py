from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, Iterable, Tuple

from .elo import EloBook

FormKey = Tuple[str, str]


class RatingStore:
    def __init__(self, form_window: int = 10) -> None:
        self.elo = EloBook()
        self.form: Dict[FormKey, Deque[int]] = defaultdict(lambda: deque(maxlen=form_window))
        self.h2h: Dict[Tuple[str, str], list[int]] = defaultdict(list)
        self.last_date: Dict[str, int] = {}
        self.matches = 0

    def pre_match(self, player: str, opponent: str, surface: str, date: int = 0) -> dict:
        surface = (surface or "hard").lower()
        p_elo, p_surf = self.elo.get(player, surface)
        o_elo, o_surf = self.elo.get(opponent, surface)
        p_form = self.form[(player, surface)]
        o_form = self.form[(opponent, surface)]
        p_wr = (sum(p_form) / len(p_form)) if p_form else 0.5
        o_wr = (sum(o_form) / len(o_form)) if o_form else 0.5
        pair = tuple(sorted((player, opponent)))
        hist = self.h2h[pair]
        if hist:
            first_rate = sum(hist) / len(hist)
            h2h_edge = (first_rate - 0.5) if player == pair[0] else (0.5 - first_rate)
        else:
            h2h_edge = 0.0
        rest_p = _rest(date, self.last_date.get(player))
        rest_o = _rest(date, self.last_date.get(opponent))
        return {
            "elo_diff": p_elo - o_elo,
            "surface_elo_diff": p_surf - o_surf,
            "recent_surface_wr_diff": p_wr - o_wr,
            "h2h_edge": h2h_edge,
            "rest_days_diff": rest_p - rest_o,
            "player_elo": p_elo,
            "opponent_elo": o_elo,
        }

    def observe(self, winner: str, loser: str, surface: str, date: int = 0) -> None:
        surface = (surface or "hard").lower()
        self.elo.update(winner, loser, surface, str(date))
        self.form[(winner, surface)].append(1)
        self.form[(loser, surface)].append(0)
        pair = tuple(sorted((winner, loser)))
        self.h2h[pair].append(1 if winner == pair[0] else 0)
        self.last_date[winner] = date
        self.last_date[loser] = date
        self.matches += 1

    def names(self) -> list[str]:
        return list(self.elo.overall.keys())

    def to_dict(self) -> dict:
        return {
            "overall": {k: float(v) for k, v in self.elo.overall.items()},
            "surface": {f"{p}|{s}": float(v) for (p, s), v in self.elo.surface.items()},
            "form": {f"{p}|{s}": list(q) for (p, s), q in self.form.items()},
            "h2h": {f"{a}|{b}": list(vals) for (a, b), vals in self.h2h.items()},
            "last_date": {k: int(v) for k, v in self.last_date.items()},
            "matches": int(self.matches),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "RatingStore":
        store = cls()
        store.elo.overall = {str(k): float(v) for k, v in (payload.get("overall") or {}).items()}
        surface = {}
        for key, val in (payload.get("surface") or {}).items():
            if "|" not in str(key):
                continue
            player, surf = str(key).rsplit("|", 1)
            surface[(player, surf)] = float(val)
        store.elo.surface = surface
        for key, vals in (payload.get("form") or {}).items():
            if "|" not in str(key):
                continue
            player, surf = str(key).rsplit("|", 1)
            store.form[(player, surf)] = deque((int(x) for x in vals), maxlen=10)
        for key, vals in (payload.get("h2h") or {}).items():
            if "|" not in str(key):
                continue
            a, b = str(key).split("|", 1)
            store.h2h[(a, b)] = [int(x) for x in vals]
        store.last_date = {str(k): int(v) for k, v in (payload.get("last_date") or {}).items()}
        store.matches = int(payload.get("matches") or 0)
        return store


def _rest(current: int, last: int | None) -> float:
    if not current or not last or last >= current:
        return 7.0
    try:
        from datetime import date

        cy, cm, cd = current // 10000, (current // 100) % 100, current % 100
        ly, lm, ld = last // 10000, (last // 100) % 100, last % 100
        delta = (date(cy, cm, cd) - date(ly, lm, ld)).days
        return float(max(0, min(delta, 60)))
    except ValueError:
        return 7.0


def replay(matches: Iterable[dict]) -> RatingStore:
    store = RatingStore()
    for row in matches:
        w = str(row.get("winner_name") or "")
        l = str(row.get("loser_name") or "")
        if not w or not l:
            continue
        try:
            d = int(row.get("tourney_date") or 0)
        except ValueError:
            d = 0
        store.observe(w, l, str(row.get("surface") or "hard"), d)
    return store
