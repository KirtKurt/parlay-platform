"""KSS1 score-matrix markets. One grid, four published books.

Pure functions. No network. No MLB/tennis imports.
"""
from __future__ import annotations

import math
from typing import Any

MAX_GOALS = 8
RHO_DEFAULT = -0.13
ABSTAIN = "ABSTAIN"
MIN_NET_EDGE = 0.02


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
    return [[cell / total for cell in row] for cell in row]


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


def devig(probabilities: dict[str, float] | None) -> dict[str, float] | None:
    """Normalize a book of nonnegative raw probabilities. Returns None if unusable."""
    if not probabilities:
        return None
    cleaned = {}
    for key, value in probabilities.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0:
            return None
        cleaned[str(key)] = number
    total = sum(cleaned.values())
    if total <= 0 or abs(total - 1.0) > 0.35:
        return None
    return {key: value / total for key, value in cleaned.items()}


def net_edges(markets: dict[str, Any], books: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    """Model probability minus de-vig market probability for each published side."""
    books = books or {}
    one = devig(books.get("1x2"))
    ou = devig(books.get("ou25"))
    btts = devig(books.get("btts"))
    dc_book = devig(books.get("dc"))
    pick_1x2 = markets["1x2_pick"]
    dc_pick = markets["double_chance_pick"]
    ou_pick = markets["ou25_pick"]
    btts_pick = markets["btts_pick"]
    p_1x2 = {"home": markets["p_home"], "draw": markets["p_draw"], "away": markets["p_away"]}
    p_dc = {"1X": markets["p_1x"], "12": markets["p_12"], "X2": markets["p_x2"]}
    p_ou = {"over": markets["p_over_25"], "under": markets["p_under_25"]}
    p_btts = {"yes": markets["p_btts_yes"], "no": markets["p_btts_no"]}
    def edge(model_p, market):
        if not market or model_p[0] not in market:
            return None
        return float(model_p[1] - market[model_p[0]])
    return {
        "market_1x2_implied": one,
        "market_dc_implied": dc_book,
        "market_ou25_implied": ou,
        "market_btts_implied": btts,
        "edge_1x2": edge((pick_1x2, markets["1x2_probability"]), one),
        "edge_double_chance": edge((dc_pick, markets["double_chance_probability"]), dc_book),
        "edge_ou25": edge((ou_pick, p_ou[ou_pick]), ou),
        "edge_btts": edge((btts_pick, p_btts[btts_pick]), btts),
        "min_net_edge": MIN_NET_EDGE,
    }


def apply_abstain(
    markets: dict[str, Any],
    *,
    min_1x2: float = 0.46,
    min_other: float = 0.55,
    min_double_chance: float = 0.70,
    component_spread: float = 0.18,
    books: dict[str, dict[str, float]] | None = None,
    require_positive_edge: bool = False,
    min_edge: float = MIN_NET_EDGE,
) -> dict[str, Any]:
    """Selective shadow book; the DC threshold exceeds its 2/3 trivial floor.

    The 0.70 default is an explicit selection rule, not fitted calibration.
    When require_positive_edge is set and a usable book exists, the published
    side must also beat that book by min_edge. Missing books do not force an abstain.
    """
    if not 2 / 3 < min_double_chance <= 1:
        raise ValueError("double-chance threshold must be greater than 2/3 and at most 1")
    out = dict(markets)
    out.update(net_edges(markets, books))
    out["1x2_published"] = markets["1x2_pick"] if markets["1x2_probability"] >= min_1x2 else ABSTAIN
    out["double_chance_published"] = (
        markets["double_chance_pick"] if markets["double_chance_probability"] >= min_double_chance else ABSTAIN
    )
    out["ou25_published"] = (
        markets["ou25_pick"] if max(markets["p_over_25"], markets["p_under_25"]) >= min_other else ABSTAIN
    )
    out["btts_published"] = (
        markets["btts_pick"] if max(markets["p_btts_yes"], markets["p_btts_no"]) >= min_other else ABSTAIN
    )
    out["component_spread_limit"] = component_spread
    out["require_positive_edge"] = bool(require_positive_edge)
    if require_positive_edge:
        checks = (
            ("1x2_published", out.get("edge_1x2")),
            ("double_chance_published", out.get("edge_double_chance")),
            ("ou25_published", out.get("edge_ou25")),
            ("btts_published", out.get("edge_btts")),
        )
        for key, edge in checks:
            if edge is not None and edge < min_edge:
                out[key] = ABSTAIN
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
