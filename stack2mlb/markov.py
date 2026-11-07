"""Inning-state simulation. Structural upgrade over independent full-game Poisson."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from stack2mlb.market import clip01


STARTER_INNINGS = 5.7
EXTRA_INNING_SHARE = 1.0 / 9.0


def _inning_rates(lambda_game: float, starter_share: float = STARTER_INNINGS / 9.0):
    lg = float(lambda_game)
    starter = np.full(9, lg * starter_share / STARTER_INNINGS)
    bullpen = np.full(9, lg * (1.0 - starter_share) / (9.0 - STARTER_INNINGS))
    rates = np.array([starter[0], starter[1], starter[2], starter[3], starter[4],
                      0.55 * starter[5] + 0.45 * bullpen[5],
                      bullpen[6], bullpen[7], bullpen[8]], dtype=float)
    return rates, bullpen


def _sample_runs(rng: np.random.Generator, rate: float, size: int) -> np.ndarray:
    return rng.poisson(lam=max(rate, 1e-6), size=size)


@dataclass
class MarkovResult:
    p_home: float
    mean_home: float
    mean_away: float
    extra_inning_rate: float
    walkoff_rate: float
    sims: int


def simulate(lambda_home: float, lambda_away: float, sims: int = 20000, seed: int = 1729) -> MarkovResult:
    if lambda_home <= 0 or lambda_away <= 0:
        raise ValueError("rates must be positive")
    rng = np.random.default_rng(seed)
    n = int(sims)
    home_rates, home_bp = _inning_rates(lambda_home)
    away_rates, away_bp = _inning_rates(lambda_away)
    home = np.zeros(n, dtype=np.int16)
    away = np.zeros(n, dtype=np.int16)
    for inn in range(9):
        away += _sample_runs(rng, away_rates[inn], n)
        if inn < 8:
            home += _sample_runs(rng, home_rates[inn], n)
        else:
            already = home > away
            need = ~already
            add = np.zeros(n, dtype=np.int16)
            if need.any():
                add[need] = _sample_runs(rng, home_rates[inn], int(need.sum()))
            home += add
    extras = home == away
    extra_n = int(extras.sum())
    if extra_n:
        h_ex = home[extras].copy()
        a_ex = away[extras].copy()
        h_rate = float(home_bp.mean() * 9 * EXTRA_INNING_SHARE)
        a_rate = float(away_bp.mean() * 9 * EXTRA_INNING_SHARE)
        tied = np.ones(extra_n, dtype=bool)
        safety = 0
        while tied.any() and safety < 12:
            k = int(tied.sum())
            a_ex[tied] += _sample_runs(rng, a_rate, k)
            h_ex[tied] += _sample_runs(rng, h_rate, k)
            tied = h_ex == a_ex
            safety += 1
        home[extras] = h_ex
        away[extras] = a_ex
    wins = home > away
    ties = home == away
    p = float(wins.mean() + 0.5 * ties.mean())
    return MarkovResult(
        p_home=clip01(p),
        mean_home=float(home.mean()),
        mean_away=float(away.mean()),
        extra_inning_rate=float(extras.mean()) if n else 0.0,
        walkoff_rate=float((~extras & (home > away)).mean()),
        sims=n,
    )


def p_home(lambda_home: float, lambda_away: float, sims: int = 8000, seed: int = 1729) -> float:
    return simulate(lambda_home, lambda_away, sims=sims, seed=seed).p_home


def _lead_probability(lead: int, lh: float, la: float) -> float:
    cap = 20
    win = tie = 0.0
    for h in range(0, cap + 1):
        ph = math.exp(h * math.log(lh) - lh - math.lgamma(h + 1))
        for a in range(0, cap + 1):
            pa = math.exp(a * math.log(la) - la - math.lgamma(a + 1))
            mass = ph * pa
            if lead + h > a:
                win += mass
            elif lead + h == a:
                tie += mass
    return win + 0.5 * tie


def expected_win_from_state(home_score, away_score, inning, half, lambda_home, lambda_away) -> float:
    remain_home = max(0, 9 - inning + (0 if half == "bottom" else 1))
    remain_away = max(0, 9 - inning + (1 if half == "top" else 0))
    if inning > 9:
        remain_home = remain_away = 1
    lh = lambda_home * (remain_home / 9.0)
    la = lambda_away * (remain_away / 9.0)
    lead = home_score - away_score
    if lh <= 0 and la <= 0:
        if lead > 0:
            return 1.0
        if lead < 0:
            return 0.0
        return 0.5
    return clip01(_lead_probability(lead, max(lh, 1e-6), max(la, 1e-6)))
