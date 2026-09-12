"""KSS1 goals engine.

Dixon-Coles score matrix, optional xG blend, optional de-vig 1X2 residual
nudge, four-market derivation, selective publish. Shadow-only until the
soccer_auto promotion gate passes.
"""
from __future__ import annotations

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


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


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
    dc_home = league_home * home_attack * away_defence
    dc_away = league_away * away_attack * home_defence
    has_xg = xg_home is not None and xg_away is not None
    if has_xg:
        lam = (1.0 - xg_weight) * dc_home + xg_weight * float(xg_home)
        mu = (1.0 - xg_weight) * dc_away + xg_weight * float(xg_away)
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
    return [[cell / total for cell in row] for row in out]


def predict_match(payload: dict[str, Any]) -> dict[str, Any]:
    mapping = map_event(
        odds_event_id=str(payload.get("odds_event_id") or payload.get("event_id") or ""),
        sport_key=str(payload.get("sport_key") or ""),
        home_team=str(payload.get("home_team") or ""),
        away_team=str(payload.get("away_team") or ""),
        commence_time=str(payload.get("commence_time") or ""),
        bbd_matches=payload.get("bbd_matches") or [],
    )
    observation = classify_observation(
        str(payload.get("commence_time") or ""),
        str(payload.get("observed_at") or payload.get("commence_time") or ""),
    )
    lam, mu, has_xg = expected_goals(
        home_attack=float(payload.get("home_attack") or 1.0),
        away_attack=float(payload.get("away_attack") or 1.0),
        home_defence=float(payload.get("home_defence") or 1.0),
        away_defence=float(payload.get("away_defence") or 1.0),
        league_home=float(payload.get("league_home") or 1.45),
        league_away=float(payload.get("league_away") or 1.15),
        xg_home=payload.get("xg_home"),
        xg_away=payload.get("xg_away"),
    )
    grid = score_matrix(lam, mu)
    grid = blend_with_market(grid, payload.get("market_1x2"))
    markets = apply_abstain(markets_from_grid(grid), min_1x2=0.40, min_other=0.51)
    # Quarantined cups stay off. Missing BBD does not blank a shadow book.
    if mapping.get("tier") == "Q" or mapping.get("goals_model_eligible") is False:
        for key in ("1x2_published", "double_chance_published", "ou25_published", "btts_published"):
            markets[key] = "ABSTAIN"
    if observation.get("action") == "reject":
        for key in ("1x2_published", "double_chance_published", "ou25_published", "btts_published"):
            markets[key] = "ABSTAIN"
    return {
        "engine_id": ENGINE_ID,
        "engine_lock_version": ENGINE_LOCK_VERSION,
        "authority": AUTHORITY,
        "public_horizon": PUBLIC_HORIZON,
        "lambda_home": lam,
        "lambda_away": mu,
        "has_xg": has_xg,
        "mapping": mapping,
        "observation": observation,
        "markets": markets,
        "automatic_prediction_allowed": False,
    }


def grade_lock(prediction: dict[str, Any], home_goals: int, away_goals: int) -> dict[str, Any]:
    actual = settle_regulation(home_goals, away_goals)
    published = prediction["markets"]
    graded = {}
    dc_hits = {
        "1X": actual["1X"] == "hit",
        "12": actual["12"] == "hit",
        "X2": actual["X2"] == "hit",
    }
    for market, (pick, truth) in {
        "1x2": (published.get("1x2_published"), actual["1x2"]),
        "ou25": (published.get("ou25_published"), actual["over_25"]),
        "btts": (published.get("btts_published"), actual["btts"]),
    }.items():
        if pick in (None, "ABSTAIN"):
            graded[market] = "abstain"
        else:
            graded[market] = "hit" if pick == truth else "miss"
    dc_pick = published.get("double_chance_published")
    if dc_pick in (None, "ABSTAIN"):
        graded["double_chance"] = "abstain"
    else:
        graded["double_chance"] = "hit" if dc_hits.get(dc_pick) else "miss"
    return {"actual": actual, "graded": graded}


def consider_public_bind(existing: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    return first_bind_wins(existing, candidate)
