"""KSS1 goals engine.

Dixon-Coles score matrix, optional xG blend, optional de-vig 1X2 residual
nudge, four-market derivation, selective publish. Shadow-only until the
soccer_auto promotion gate passes.
"""
from __future__ import annotations

import math
from typing import Any

from soccer_auto.kss1_identity import map_event
from soccer_auto.kss1_lock import (
    ENGINE_LOCK_VERSION,
    PUBLIC_HORIZON,
    classify_observation,
    first_bind_wins,
)
from soccer_auto.kss1_markets import apply_abstain, markets_from_grid, score_matrix, settle_regulation

ENGINE_ID = "kss1-goals-v1"
AUTHORITY = "SHADOW_LEARNING"
GOAL_INPUT_DEFAULTS = {
    "home_attack": 1.0, "away_attack": 1.0,
    "home_defence": 1.0, "away_defence": 1.0,
    "league_home": 1.45, "league_away": 1.15,
}


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _goal_input(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("goal inputs must be finite nonnegative numbers")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("goal inputs must be finite nonnegative numbers")
    return number


def expected_goals(
    *,
    home_attack: float = 1.0,
    away_attack: float = 1.0,
    home_defence: float = 1.0,
    away_defence: float = 1.0,
    league_home: float = 1.45,
    league_away: float = 1.15,
    xg_home: float | None = None,
    xg_away: float | None = None,
    xg_weight: float = 0.35,
) -> tuple[float, float, bool]:
    dc_home = _goal_input(league_home) * _goal_input(home_attack) * _goal_input(away_defence)
    dc_away = _goal_input(league_away) * _goal_input(away_attack) * _goal_input(home_defence)
    has_xg = xg_home is not None and xg_away is not None
    if has_xg:
        lam = (1.0 - xg_weight) * dc_home + xg_weight * _goal_input(xg_home)
        mu = (1.0 - xg_weight) * dc_away + xg_weight * _goal_input(xg_away)
    else:
        lam, mu = dc_home, dc_away
    return _clip(lam, 0.05, 5.5), _clip(mu, 0.05, 5.5), bool(has_xg)


def blend_with_market(grid: list[list[float]], market_1x2: dict[str, float] | None, weight: float = 0.22) -> list[list[float]]:
    if not market_1x2:
        return grid
    markets = markets_from_grid(grid)
    target = {
        "home": float(market_1x2.get("home") or 0.0),
        "draw": float(market_1x2.get("draw") or 0.0),
        "away": float(market_1x2.get("away") or 0.0),
    }
    if min(target.values()) < 0 or abs(sum(target.values()) - 1.0) > 0.08:
        return grid
    scale = {
        "home": 1.0 + weight * (target["home"] - markets["p_home"]),
        "draw": 1.0 + weight * (target["draw"] - markets["p_draw"]),
        "away": 1.0 + weight * (target["away"] - markets["p_away"]),
    }
    out = []
    for i, row in enumerate(grid):
        new_row = []
        for j, value in enumerate(row):
            if i > j:
                factor = scale["home"]
            elif i < j:
                factor = scale["away"]
            else:
                factor = scale["draw"]
            new_row.append(max(0.0, value * factor))
        out.append(new_row)
    total = sum(sum(row) for row in out)
    if total <= 0:
        return grid
    return [[cell / total for cell in row] for cell in row]
