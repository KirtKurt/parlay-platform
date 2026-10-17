"""Pitcher-adjusted Elo / Pi-rating. Low capacity on purpose.

A 10-3 blowout moves a team about K/2 ≈ 3 points. That is the point:
Elo refuses to become the 77% model after one night against Colorado.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from stack2mlb import ELO_K_PITCHER, ELO_K_TEAM, ELO_SCALE, HOME_FIELD_ELO, STARTER_WEIGHT
from stack2mlb.market import clip01


def expected(diff: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-float(diff) / ELO_SCALE))


@dataclass
class EloBook:
    teams: dict[str, float] = field(default_factory=dict)
    pitchers: dict[str, float] = field(default_factory=dict)
    default: float = 1500.0

    def team(self, team_id: str) -> float:
        return self.teams.get(str(team_id), self.default)

    def pitcher(self, pitcher_id: str | None) -> float:
        if not pitcher_id:
            return self.default
        return self.pitchers.get(str(pitcher_id), self.default)

    def p_home(
        self,
        home_id: str,
        away_id: str,
        home_starter: str | None = None,
        away_starter: str | None = None,
        home_field: float = HOME_FIELD_ELO,
    ) -> float:
        team_diff = self.team(home_id) - self.team(away_id)
        pitch_diff = self.pitcher(home_starter) - self.pitcher(away_starter)
        blended = (1.0 - STARTER_WEIGHT) * team_diff + STARTER_WEIGHT * pitch_diff
        return clip01(expected(blended + home_field))

    def update(
        self,
        home_id: str,
        away_id: str,
        home_win: bool,
        home_starter: str | None = None,
        away_starter: str | None = None,
        margin: float | None = None,
    ) -> None:
        p = self.p_home(home_id, away_id, home_starter, away_starter)
        y = 1.0 if home_win else 0.0
        scale = 1.0
        if margin is not None:
            scale = min(2.0, (abs(float(margin)) ** 0.5) / 2.0 + 0.5)
        delta = ELO_K_TEAM * scale * (y - p)
        self.teams[str(home_id)] = self.team(home_id) + delta
        self.teams[str(away_id)] = self.team(away_id) - delta
        if home_starter:
            hp = expected(self.pitcher(home_starter) - self.pitcher(away_starter) + HOME_FIELD_ELO)
            self.pitchers[str(home_starter)] = self.pitcher(home_starter) + ELO_K_PITCHER * scale * (y - hp)
        if away_starter:
            ap = expected(self.pitcher(away_starter) - self.pitcher(home_starter) - HOME_FIELD_ELO)
            self.pitchers[str(away_starter)] = self.pitcher(away_starter) + ELO_K_PITCHER * scale * ((1.0 - y) - ap)
