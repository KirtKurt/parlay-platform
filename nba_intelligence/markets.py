"""NBA market-specific wrappers. Same two-path math; different market label."""
from __future__ import annotations

from typing import Mapping, Sequence

from .layer import evaluate_game

SUPPORTED_MARKETS = ("moneyline", "spread", "total", "team_total", "player")


def evaluate_market(
    *,
    market: str,
    event_id: str,
    home: str,
    away: str,
    p_fundamental_home: float,
    p_market_aware_home: float,
    p_market_home: float,
    active_fundamental_signals: Sequence[str] = (),
    active_market_signals: Sequence[str] = (),
    collected_signals: Sequence[str] = (),
    shap: Mapping[str, float] | None = None,
) -> dict:
    if market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported NBA market: {market}")
    return evaluate_game(
        event_id=event_id,
        home=home,
        away=away,
        p_fundamental_home=p_fundamental_home,
        p_market_aware_home=p_market_aware_home,
        p_market_home=p_market_home,
        market=market,
        active_fundamental_signals=active_fundamental_signals,
        active_market_signals=active_market_signals,
        collected_signals=collected_signals,
        shap=shap,
    )
