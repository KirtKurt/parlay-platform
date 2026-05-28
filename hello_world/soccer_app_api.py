from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from soccer_signal_api import soccer_match_signals, soccer_parlays
from universal_market_language import market_language_status, prediction_label_explanation, status_filter_explanation

DISPLAY_CONFIDENCE_SCORES = True

STATUS_PRIORITY = {
    "Clean Edge": 0,
    "Playable Edge": 1,
    "Chaos Match": 2,
    "Watchlist Edge": 3,
    "No Clean Edge": 4,
}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _safe_pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value) * 100, 1)
    except Exception:
        return None


def _format_kickoff(iso_value: Optional[str]) -> Optional[str]:
    if not iso_value:
        return None
    try:
        dt = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%a %-I:%M %p ET")
    except Exception:
        return iso_value


def _market_language(item: Dict[str, Any]) -> Dict[str, Any]:
    return item.get("public_market_language") or {
        "public_prediction": "Pass / No Clean Edge",
        "prediction_label_explanation": prediction_label_explanation("Pass / No Clean Edge"),
        "market_status": "No Clean Edge",
        "status_filter_explanation": status_filter_explanation("No Clean Edge"),
        "best_use": "Avoid / No Bet",
        "market_intelligence_tags": [],
        "public_explanation": "No clean edge is published yet. The market is being tracked until movement becomes clearer.",
        "display_confidence_scores": DISPLAY_CONFIDENCE_SCORES,
    }


def _match_card(match: Dict[str, Any]) -> Dict[str, Any]:
    language = _market_language(match)
    outcomes = match.get("outcomes") or {}
    ordered_outcomes = []
    for key in ("home", "draw", "away"):
        row = outcomes.get(key) or {}
        ordered_outcomes.append({
            "outcome": key,
            "label": row.get("team") or ("Draw" if key == "draw" else key.title()),
            "american_odds": row.get("consensus_price"),
            "decimal_odds": row.get("decimal_odds"),
            "consensus_probability_pct": _safe_pct(row.get("consensus_probability")),
            "signal": row.get("signal"),
        })
    title = match.get("match") or f"{match.get('away_team')} at {match.get('home_team')}"
    status = language.get("market_status")
    prediction_label = language.get("public_prediction")
    confidence_score = match.get("confidence_score") or match.get("confidence")
    return {
        "card_type": "individual_match",
        "title": title,
        "subtitle": match.get("league"),
        "league": match.get("league"),
        "kickoff": _format_kickoff(match.get("commence_time")),
        "prediction_label": prediction_label,
        "prediction_label_explanation": language.get("prediction_label_explanation") or prediction_label_explanation(prediction_label),
        "market_status": status,
        "status_priority": STATUS_PRIORITY.get(status, 99),
        "status_filter_explanation": language.get("status_filter_explanation") or status_filter_explanation(status),
        "filter_reason": language.get("status_filter_explanation") or status_filter_explanation(status),
        "best_use": language.get("best_use"),
        "market_intelligence_tags": language.get("market_intelligence_tags", []),
        "why": language.get("public_explanation"),
        "display_confidence_scores": DISPLAY_CONFIDENCE_SCORES,
        "confidence_score": confidence_score,
        "confidence_label": status,
        "hot_side": match.get("hot_side"),
        "hot_outcome": match.get("hot_outcome"),
        "books_tracked": match.get("books_tracked"),
        "outcomes": ordered_outcomes,
    }


def _parlay_card(combo: Dict[str, Any]) -> Dict[str, Any]:
    language = _market_language(combo)
    prediction_label = language.get("public_prediction")
    status = language.get("market_status")
    legs = []
    for leg in combo.get("legs") or []:
        legs.append({
            "match": leg.get("match"),
            "selection": leg.get("selection"),
            "outcome": leg.get("outcome"),
            "american_odds": leg.get("american_odds"),
            "decimal_odds": leg.get("decimal_odds"),
            "consensus_probability_pct": _safe_pct(leg.get("consensus_probability")),
        })
    return {
        "card_type": "three_match_soccer_parlay",
        "rank": combo.get("rank"),
        "title": combo.get("combo"),
        "subtitle": "3-match soccer parlay · 27-combo market",
        "prediction_label": prediction_label,
        "prediction_label_explanation": language.get("prediction_label_explanation") or prediction_label_explanation(prediction_label),
        "market_status": status,
        "status_filter_explanation": language.get("status_filter_explanation") or status_filter_explanation(status),
        "filter_reason": language.get("status_filter_explanation") or status_filter_explanation(status),
        "best_use": language.get("best_use"),
        "market_intelligence_tags": language.get("market_intelligence_tags", []),
        "why": language.get("public_explanation"),
        "display_confidence_scores": DISPLAY_CONFIDENCE_SCORES,
        "confidence_score": combo.get("confidence_score") or combo.get("signal_score_internal"),
        "confidence_label": combo.get("confidence_band") or status,
        "parlay_decimal_odds": combo.get("parlay_decimal_odds"),
        "parlay_american_odds": combo.get("parlay_american_odds"),
        "implied_win_probability_pct": combo.get("implied_win_probability_pct"),
        "legs": legs,
    }


def _status_filters(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for card in cards:
        status = card.get("market_status") or "No Clean Edge"
        counts[status] = counts.get(status, 0) + 1
    filters = []
    for status, priority in sorted(STATUS_PRIORITY.items(), key=lambda item: item[1]):
        filters.append({
            "status": status,
            "count": counts.get(status, 0),
            "why_this_filter_exists": status_filter_explanation(status),
            "display_confidence_scores": DISPLAY_CONFIDENCE_SCORES,
        })
    return filters


def soccer_app_cards(limit: int = 40, top_matches: int = 25, top_parlays: int = 3, league: Optional[str] = None) -> Dict[str, Any]:
    matches_payload = soccer_match_signals(limit=limit)
    match_cards = [_match_card(match) for match in matches_payload.get("matches", [])]
    if league:
        match_cards = [card for card in match_cards if card.get("league") == league]
    match_cards.sort(key=lambda card: (card.get("status_priority", 99), card.get("kickoff") or "", card.get("title") or ""))

    league_sections: Dict[str, List[Dict[str, Any]]] = {}
    for card in match_cards:
        league_sections.setdefault(card.get("league") or "unknown", []).append(card)

    parlay_payload = soccer_parlays(limit=limit)
    parlay_cards = [_parlay_card(combo) for combo in parlay_payload.get("ranked_combinations", [])[:top_parlays]] if parlay_payload.get("parlays_ready") else []

    return {
        "ok": True,
        "sport": "soccer",
        "view": "customer_app_cards",
        "model": matches_payload.get("model"),
        "feature_version": "soccer_customer_cards_v3_status_filter_reasons_with_confidence_scores",
        "asof": matches_payload.get("asof"),
        "market_language": market_language_status(),
        "display_confidence_scores": DISPLAY_CONFIDENCE_SCORES,
        "raw_json_hidden_from_customer": True,
        "counts": {
            "matches": len(match_cards),
            "league_sections": len(league_sections),
            "parlays": len(parlay_cards),
        },
        "status_filters": _status_filters(match_cards),
        "top_match_cards": match_cards[:top_matches],
        "league_sections": {key: value[:top_matches] for key, value in sorted(league_sections.items())},
        "top_parlay_cards": parlay_cards,
        "parlays_ready": bool(parlay_payload.get("parlays_ready")),
        "parlay_message": parlay_payload.get("reason") if not parlay_payload.get("parlays_ready") else None,
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    params = event.get("queryStringParameters") or {}
    try:
        if method == "GET" and path in {"/v1/soccer/app/cards", "/v1/app/soccer/cards"}:
            return _resp(200, soccer_app_cards(
                limit=min(int(params.get("limit") or 40), 200),
                top_matches=min(int(params.get("top_matches") or 25), 100),
                top_parlays=min(int(params.get("top_parlays") or 3), 10),
                league=params.get("league"),
            ))
        return _resp(404, {"ok": False, "sport": "soccer", "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "soccer", "error": str(exc)})
