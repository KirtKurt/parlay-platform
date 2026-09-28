"""Market-blind feature contract for NBA fundamentals.

A name is market-derived if it contains a banned token or is an alias of price.
This module never fabricates basketball stats; it only accepts or rejects names.
"""
from __future__ import annotations

from typing import Iterable

from inqsi_intelligence.favorite_bias import assert_fundamentals_market_free

BANNED_MARKET_TOKENS = (
    "odds",
    "moneyline",
    "spread",
    "total",
    "implied",
    "consensus",
    "market",
    "book",
    "vig",
    "steam",
    "line_move",
    "linemove",
    "opening",
    "closing",
    "public_pct",
    "ticket_pct",
    "favorite_label",
    "underdog_label",
    "reverse_line",
)

ALLOWED_FUNDAMENTAL_FAMILIES = (
    "offensive_efficiency",
    "defensive_efficiency",
    "net_rating",
    "pace",
    "expected_possessions",
    "shot_profile",
    "efg",
    "ts_pct",
    "three_point",
    "rim",
    "free_throw_rate",
    "rebounding",
    "turnover",
    "half_court",
    "transition",
    "lineup",
    "availability",
    "injury",
    "minutes_restriction",
    "rotation",
    "bench",
    "five_man",
    "on_off",
    "matchup",
    "rest",
    "back_to_back",
    "travel",
    "schedule_density",
    "home_court",
    "recent_form",
    "season_prior",
)


def filter_fundamental_features(feature_names: Iterable[str]) -> tuple[str, ...]:
    kept = tuple(str(name) for name in feature_names if not _is_market_name(name))
    assert_fundamentals_market_free(kept, forbidden_tokens=BANNED_MARKET_TOKENS)
    return kept


def _is_market_name(name: str) -> bool:
    lowered = str(name).lower()
    return any(token in lowered for token in BANNED_MARKET_TOKENS)
