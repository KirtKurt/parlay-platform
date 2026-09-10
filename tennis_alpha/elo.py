from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple

SURFACES = ("hard", "clay", "grass", "carpet")
K = 32.0
BASE = 1500.0


def _expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((b - a) / 400.0))


@dataclass
class EloBook:
    overall: Dict[str, float] = field(default_factory=dict)
    surface: Dict[Tuple[str, str], float] = field(default_factory=dict)
    last_date: Dict[str, str] = field(default_factory=dict)

    def get(self, player: str, surface: str) -> Tuple[float, float]:
        return (
            self.overall.get(player, BASE),
            self.surface.get((player, surface), BASE),
        )

    def update(self, winner: str, loser: str, surface: str, date: str) -> None:
        surface = (surface or "hard").lower()
        if surface not in SURFACES:
            surface = "hard"
        wo, ws = self.get(winner, surface)
        lo, ls = self.get(loser, surface)
        ew, el = _expected(wo, lo), _expected(lo, wo)
        ews, els = _expected(ws, ls), _expected(ls, ws)
        self.overall[winner] = wo + K * (1.0 - ew)
        self.overall[loser] = lo + K * (0.0 - el)
        self.surface[(winner, surface)] = ws + K * (1.0 - ews)
        self.surface[(loser, surface)] = ls + K * (0.0 - els)
        self.last_date[winner] = date
        self.last_date[loser] = date


def replay(matches: Iterable[dict]) -> EloBook:
    book = EloBook()
    for row in matches:
        w = str(row.get("winner_name") or row.get("winner") or "")
        l = str(row.get("loser_name") or row.get("loser") or "")
        if not w or not l or w == l:
            continue
        book.update(w, l, str(row.get("surface") or "hard"), str(row.get("tourney_date") or ""))
    return book
