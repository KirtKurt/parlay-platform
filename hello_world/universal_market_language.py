from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

LANGUAGE_VERSION = "universal_market_language_v1"

PRIMARY_PREDICTIONS = {
    "HOME_TEAM_WIN": "Home Team Win",
    "AWAY_TEAM_WIN": "Away Team Win",
    "FAVORITE_WIN": "Favorite Win",
    "UNDERDOG_WIN": "Underdog Win",
    "DRAW_WATCH": "Draw Watch",
    "UPSET_WATCH": "Upset Watch",
    "CHAOS_MATCH": "Chaos Match",
    "HOT_SIDE_PRESSURE": "Hot Side Pressure",
    "PASS_NO_CLEAN_EDGE": "Pass / No Clean Edge",
}

MARKET_STATUS = {
    "CLEAN_EDGE": "Clean Edge",
    "PLAYABLE_EDGE": "Playable Edge",
    "WATCHLIST_EDGE": "Watchlist Edge",
    "CHAOS_MATCH": "Chaos Match",
    "NO_CLEAN_EDGE": "No Clean Edge",
}

BEST_USE = {
    "SINGLE_MATCH_PICK": "Single Match Pick",
    "PARLAY_ANCHOR": "Parlay Anchor",
    "PARLAY_VARIABLE": "Parlay Variable",
    "WATCHLIST_ONLY": "Watchlist Only",
    "AVOID_NO_BET": "Avoid / No Bet",
}

MARKET_INTELLIGENCE_TERMS = {
    "three_way_market_compression": "3-Way Market Compression",
    "compressed_three_way_market": "3-Way Market Compression",
    "cross_book_confirmation": "Cross-Book Confirmation",
    "multi_book_move": "Cross-Book Confirmation",
    "draw_pressure": "Draw Pressure",
    "favorite_separation": "Favorite Separation",
    "favorite_pressure": "Favorite Separation",
    "favorite_resistance": "Favorite Resistance",
    "favorite_not_separating": "Favorite Resistance",
    "underdog_compression": "Underdog Compression",
    "dog_tightening": "Underdog Compression",
    "non_favorite_pressure": "Underdog/Draw Pressure",
    "spread_disagreement": "Spread Disagreement",
    "spread_supports_hot_side": "Spread Support",
    "total_market_support": "Total Market Support",
    "late_market_shift": "Late Market Shift",
    "low_goal_trap": "Low-Goal Trap",
    "high_variance_match": "High-Variance Match",
}

STATUS_FILTER_EXPLANATIONS = {
    MARKET_STATUS["CLEAN_EDGE"]: "Shown when the market is giving a clear side: published pressure plus cross-book confirmation or a multi-book move.",
    MARKET_STATUS["PLAYABLE_EDGE"]: "Shown when a side has enough market pressure to publish, but the signal is not strong enough to call it a clean anchor.",
    MARKET_STATUS["CHAOS_MATCH"]: "Shown when the match has 3-way market compression, draw pressure, non-favorite pressure, or cross-book tension. This is a volatility label, not a confidence score.",
    MARKET_STATUS["WATCHLIST_EDGE"]: "Shown when early pressure exists but the market has not confirmed enough to publish a pick. Keep tracking movement before using it.",
    MARKET_STATUS["NO_CLEAN_EDGE"]: "Shown when the market has not separated enough. The safest customer-facing use is pass, avoid, or keep tracking.",
}

PRIMARY_PREDICTION_EXPLANATIONS = {
    PRIMARY_PREDICTIONS["HOT_SIDE_PRESSURE"]: "The side is receiving the strongest current market pressure, but the card still checks whether that pressure is clean, conflicted, or only a watchlist signal.",
    PRIMARY_PREDICTIONS["CHAOS_MATCH"]: "The match is not clean enough for a simple side. The market is compressed, conflicted, or showing draw/non-favorite pressure.",
    PRIMARY_PREDICTIONS["DRAW_WATCH"]: "The draw outcome is receiving enough market attention to track, especially in a 3-way soccer market.",
    PRIMARY_PREDICTIONS["UPSET_WATCH"]: "The side getting pressure is not the current market leader, which can signal underdog or non-favorite pressure.",
    PRIMARY_PREDICTIONS["PASS_NO_CLEAN_EDGE"]: "The model is intentionally not publishing a side because the market has not separated enough.",
    PRIMARY_PREDICTIONS["HOME_TEAM_WIN"]: "The home side is the published market read when supported by the active signal rules.",
    PRIMARY_PREDICTIONS["AWAY_TEAM_WIN"]: "The away side is the published market read when supported by the active signal rules.",
    PRIMARY_PREDICTIONS["FAVORITE_WIN"]: "The favorite is the published market read when the favorite is separating cleanly.",
    PRIMARY_PREDICTIONS["UNDERDOG_WIN"]: "The underdog is the published market read when dog pressure is strong enough to clear the signal rules.",
}


def reason_codes_to_tags(reason_codes: Optional[Iterable[str]]) -> List[str]:
    seen = set()
    tags: List[str] = []
    for code in reason_codes or []:
        tag = MARKET_INTELLIGENCE_TERMS.get(str(code), str(code).replace("_", " ").title())
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def market_status_from_signals(*, prediction_status: Optional[str], reason_codes: Optional[Iterable[str]] = None) -> str:
    codes = set(reason_codes or [])
    prediction_status = prediction_status or "NO_EDGE"
    if "compressed_three_way_market" in codes and ("draw_pressure" in codes or "non_favorite_pressure" in codes):
        return MARKET_STATUS["CHAOS_MATCH"]
    if prediction_status.startswith("PUBLISHED") and ("multi_book_move" in codes or "cross_book_confirmation" in codes):
        return MARKET_STATUS["CLEAN_EDGE"]
    if prediction_status.startswith("PUBLISHED"):
        return MARKET_STATUS["PLAYABLE_EDGE"]
    if prediction_status == "WATCHLIST":
        return MARKET_STATUS["WATCHLIST_EDGE"]
    return MARKET_STATUS["NO_CLEAN_EDGE"]


def best_use_from_status(status: str, *, is_parlay: bool = False) -> str:
    if status == MARKET_STATUS["CLEAN_EDGE"]:
        return BEST_USE["PARLAY_ANCHOR"] if is_parlay else BEST_USE["SINGLE_MATCH_PICK"]
    if status == MARKET_STATUS["PLAYABLE_EDGE"]:
        return BEST_USE["PARLAY_VARIABLE"] if is_parlay else BEST_USE["SINGLE_MATCH_PICK"]
    if status == MARKET_STATUS["WATCHLIST_EDGE"]:
        return BEST_USE["WATCHLIST_ONLY"] if not is_parlay else BEST_USE["PARLAY_VARIABLE"]
    if status == MARKET_STATUS["CHAOS_MATCH"]:
        return BEST_USE["AVOID_NO_BET"]
    return BEST_USE["AVOID_NO_BET"]


def status_filter_explanation(status: Optional[str]) -> str:
    return STATUS_FILTER_EXPLANATIONS.get(
        status or MARKET_STATUS["NO_CLEAN_EDGE"],
        "Shown based on the public market-status rules for this sport silo.",
    )


def prediction_label_explanation(label: Optional[str]) -> str:
    if not label:
        return PRIMARY_PREDICTION_EXPLANATIONS[PRIMARY_PREDICTIONS["PASS_NO_CLEAN_EDGE"]]
    if label in PRIMARY_PREDICTION_EXPLANATIONS:
        return PRIMARY_PREDICTION_EXPLANATIONS[label]
    if str(label).endswith(" Win"):
        return "The named side is the published market read when supported by the active signal rules."
    return "This label is based on the strongest current market read after applying the sport-specific signal rules."


def soccer_public_prediction(*, hot_outcome: Optional[str], current_leader: Optional[str], home_team: Optional[str], away_team: Optional[str], hot_label: Optional[str], prediction_status: Optional[str], reason_codes: Optional[Iterable[str]]) -> str:
    status = market_status_from_signals(prediction_status=prediction_status, reason_codes=reason_codes)
    if status == MARKET_STATUS["CHAOS_MATCH"]:
        return PRIMARY_PREDICTIONS["CHAOS_MATCH"]
    if prediction_status == "NO_EDGE" or not hot_outcome:
        return PRIMARY_PREDICTIONS["PASS_NO_CLEAN_EDGE"]
    if hot_outcome == "draw":
        return PRIMARY_PREDICTIONS["DRAW_WATCH"]
    if hot_outcome != current_leader:
        return PRIMARY_PREDICTIONS["UPSET_WATCH"]
    if hot_outcome == "home":
        return f"{home_team or hot_label} Win" if home_team else PRIMARY_PREDICTIONS["HOME_TEAM_WIN"]
    if hot_outcome == "away":
        return f"{away_team or hot_label} Win" if away_team else PRIMARY_PREDICTIONS["AWAY_TEAM_WIN"]
    return PRIMARY_PREDICTIONS["HOT_SIDE_PRESSURE"]


def public_explanation(*, prediction: Optional[str], market_status: str, tags: List[str]) -> str:
    if market_status == MARKET_STATUS["NO_CLEAN_EDGE"]:
        return "No clean edge is published yet. The market is being tracked until movement becomes clearer."
    if market_status == MARKET_STATUS["CHAOS_MATCH"]:
        return "This match is showing compression, draw pressure, or cross-book tension. Treat it as high variance instead of a clean anchor."
    if tags:
        return f"{prediction or 'Market pressure detected'} backed by {', '.join(tags[:3])}."
    return prediction or "Market pressure detected."


def build_public_market_language(*, sport: str, prediction_status: Optional[str], reason_codes: Optional[Iterable[str]] = None, prediction: Optional[str] = None, is_parlay: bool = False, soccer_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    reason_codes = list(reason_codes or [])
    tags = reason_codes_to_tags(reason_codes)
    status = market_status_from_signals(prediction_status=prediction_status, reason_codes=reason_codes)
    public_prediction = prediction or PRIMARY_PREDICTIONS["HOT_SIDE_PRESSURE"]
    if sport == "soccer" and soccer_context:
        public_prediction = soccer_public_prediction(
            hot_outcome=soccer_context.get("hot_outcome"),
            current_leader=soccer_context.get("current_leader"),
            home_team=soccer_context.get("home_team"),
            away_team=soccer_context.get("away_team"),
            hot_label=soccer_context.get("hot_label"),
            prediction_status=prediction_status,
            reason_codes=reason_codes,
        )
    return {
        "language_version": LANGUAGE_VERSION,
        "public_prediction": public_prediction,
        "prediction_label_explanation": prediction_label_explanation(public_prediction),
        "market_status": status,
        "status_filter_explanation": status_filter_explanation(status),
        "best_use": best_use_from_status(status, is_parlay=is_parlay),
        "market_intelligence_tags": tags,
        "public_explanation": public_explanation(prediction=prediction, market_status=status, tags=tags),
        "display_confidence_scores": True,
    }


def market_language_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "language_version": LANGUAGE_VERSION,
        "scope": "universal_all_sports",
        "rule": "Use market-status language, market-intelligence tags, and user-facing confidence score fields when supported by the sport card layer.",
        "primary_predictions": list(PRIMARY_PREDICTIONS.values()),
        "primary_prediction_explanations": PRIMARY_PREDICTION_EXPLANATIONS,
        "market_statuses": list(MARKET_STATUS.values()),
        "status_filter_explanations": STATUS_FILTER_EXPLANATIONS,
        "best_uses": list(BEST_USE.values()),
        "market_intelligence_terms": sorted(set(MARKET_INTELLIGENCE_TERMS.values())),
        "display_confidence_scores": True,
    }
