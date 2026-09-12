"""KSS1 score-matrix markets. One grid, four published books.

Pure functions. No network. No MLB/tennis imports.
"""
from __future__ import annotations

import math
from typing import Any

MAX_GOALS = 8
RHO_DEFAULT = -0.13
ABSTAIN = "ABSTAIN"


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def score_matrix(lambda_home: float, lambda_away: float, rho: float = RHO_DEFAULT, max_goals: int = MAX_GOALS) -> list[list[float]]:
    lam = min(max(float(lambda_home), 0.05), 5.5)
    mu = min(max(float(lambda_away), 0.05), 5.5)
    home = [_poisson_pmf(i, lam) for i in range(max_goals + 1)]
    away = [_poisson_pmf(j, mu) for j in range(max_goals + 1)]
    grid = [[home[i] * away[j] for j in range(max_goals + 1)] for i in range(max_goals + 1)]
    grid[0][0] *= 1 - lam * mu * rho
    grid[0][1] *= 1 + lam * rho
    grid[1][0] *= 1 + mu * rho
    grid[1][1] *= 1 - rho
    if any(cell < 0 for row in grid for cell in row):
        raise ValueError("invalid Dixon-Coles correction")
    total = sum(sum(row) for row in grid)
    if total <= 0:
        raise ValueError("empty score matrix")
    return [[cell / total for cell in row] for row in grid]


def _sum_where(grid: list[list[float]], pred) -> float:
    total = 0.0
    for i, row in enumerate(grid):
        for j, value in enumerate(row):
            if pred(i, j):
                total += value
    return float(total)


def markets_from_grid(grid: list[list[float]]) -> dict[str, Any]:
    if not grid or len(grid) != len(grid[0]):
        raise ValueError("grid must be square")
    p_home = _sum_where(grid, lambda i, j: i > j)
    p_draw = _sum_where(grid, lambda i, j: i == j)
    p_away = _sum_where(grid, lambda i, j: i < j)
    p_over_25 = _sum_where(grid, lambda i, j: i + j >= 3)
    p_btts = _sum_where(grid, lambda i, j: i >= 1 and j >= 1)
    ones = p_home + p_draw + p_away
    if abs(ones - 1.0) > 1e-6:
        raise ValueError("1X2 probabilities must sum to 1")
    pick_1x2 = ("home", "draw", "away")[max(range(3), key=lambda k: (p_home, p_draw, p_away)[k])]
    dc = {
        "1X": p_home + p_draw,
        "12": p_home + p_away,
        "X2": p_draw + p_away,
    }
    dc_pick = max(dc, key=dc.get)
    return {
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "1x2_pick": pick_1x2,
        "1x2_probability": max(p_home, p_draw, p_away),
        "p_1x": dc["1X"],
        "p_12": dc["12"],
        "p_x2": dc["X2"],
        "double_chance_pick": dc_pick,
        "double_chance_probability": dc[dc_pick],
        "p_over_25": p_over_25,
        "p_under_25": 1.0 - p_over_25,
        "ou25_pick": "over" if p_over_25 >= 0.5 else "under",
        "p_btts_yes": p_btts,
        "p_btts_no": 1.0 - p_btts,
        "btts_pick": "yes" if p_btts >= 0.5 else "no",
    }


def apply_abstain(markets: dict[str, Any], *, min_1x2: float = 0.46, min_other: float = 0.55, component_spread: float = 0.18) -> dict[str, Any]:
    """Selective book. Coverage of a fixture does not force every market."""
    out = dict(markets)
    out["1x2_published"] = markets["1x2_pick"] if markets["1x2_probability"] >= min_1x2 else ABSTAIN
    out["double_chance_published"] = (
        markets["double_chance_pick"] if markets["double_chance_probability"] >= min_other else ABSTAIN
    )
    out["ou25_published"] = (
        markets["ou25_pick"] if max(markets["p_over_25"], markets["p_under_25"]) >= min_other else ABSTAIN
    )
    out["btts_published"] = (
        markets["btts_pick"] if max(markets["p_btts_yes"], markets["p_btts_no"]) >= min_other else ABSTAIN
    )
    out["component_spread_limit"] = component_spread
    return out


def settle_regulation(home_goals: int, away_goals: int) -> dict[str, str]:
    if home_goals < 0 or away_goals < 0:
        raise ValueError("goals must be >= 0")
    if home_goals > away_goals:
        result = "home"
    elif home_goals < away_goals:
        result = "away"
    else:
        result = "draw"
    return {
        "1x2": result,
        "1X": "hit" if result in ("home", "draw") else "miss",
        "12": "hit" if result in ("home", "away") else "miss",
        "X2": "hit" if result in ("draw", "away") else "miss",
        "over_25": "over" if home_goals + away_goals >= 3 else "under",
        "btts": "yes" if home_goals >= 1 and away_goals >= 1 else "no",
    }
