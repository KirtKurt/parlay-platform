from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


SUPPORTED_SPORTS = {"mlb", "nba", "ncaam", "nhl", "soccer"}

SPORT_CONFIG = {
    "mlb": {
        "target_accuracy_pct": 65,
        "min_publish_confidence": 65,
        "min_training_games_soft": 100,
        "min_training_games_useful": 750,
        "model_scope": "individual_game_winner_only",
    },
    "nba": {
        "target_accuracy_pct": 65,
        "min_publish_confidence": 65,
        "min_training_games_soft": 100,
        "min_training_games_useful": 750,
        "model_scope": "individual_game_winner_only",
    },
    "ncaam": {
        "target_accuracy_pct": 65,
        "min_publish_confidence": 65,
        "min_training_games_soft": 250,
        "min_training_games_useful": 1500,
        "model_scope": "individual_game_winner_only",
    },
    "nhl": {
        "target_accuracy_pct": 65,
        "min_publish_confidence": 65,
        "min_training_games_soft": 150,
        "min_training_games_useful": 1000,
        "model_scope": "individual_game_winner_only",
    },
    "soccer": {
        "target_accuracy_pct": 65,
        "min_publish_confidence": 65,
        "min_training_games_soft": 250,
        "min_training_games_useful": 2000,
        "model_scope": "individual_match_result_only",
        "note": "Soccer is 3-way: home/draw/away. It must remain separate from 2-way sports.",
    },
}


@dataclass
class WinnerPrediction:
    sport: str
    game_key: str
    predicted_team: Optional[str]
    prediction_status: str
    confidence: float
    confidence_label: str
    reason_codes: List[str]
    user_message: Dict[str, str]


def _confidence_label(confidence: float) -> str:
    if confidence >= 75:
        return "strong"
    if confidence >= 65:
        return "published"
    if confidence >= 58:
        return "watchlist"
    return "no edge"


def _build_user_message(team: Optional[str], status: str, confidence_label: str, reasons: List[str]) -> Dict[str, str]:
    if not team or status == "NO_EDGE":
        return {
            "hot_side": "Hot Side: No clear edge",
            "prediction": "Prediction: No published winner prediction",
            "reason": "Reason: signals do not meet the publish threshold",
            "confidence": f"Confidence: {confidence_label}",
        }

    if confidence_label == "watchlist":
        prediction = f"{team} watchlist"
    else:
        prediction = f"{team} winner lean"

    return {
        "hot_side": f"Hot Side: {team} pressure",
        "prediction": f"Prediction: {prediction}",
        "reason": f"Reason: {' + '.join(reasons) if reasons else 'qualified market signal'}",
        "confidence": f"Confidence: {confidence_label}",
    }


def score_rule_based_winner_candidate(game_signal: Dict[str, Any]) -> WinnerPrediction:
    """Temporary rule-based winner gate until sport-specific ML is trained.

    This does not pretend to be the final ML model. It creates a consistent
    individual-game prediction object and enforces the 65% publish gate.
    """
    sport = (game_signal.get("sport") or "mlb").lower()
    if sport not in SUPPORTED_SPORTS:
        raise ValueError(f"Unsupported sport for winner prediction: {sport}")

    game_key = game_signal.get("game_key") or game_signal.get("game_id") or "UNKNOWN"
    team = game_signal.get("predicted_team") or game_signal.get("hot_team")
    base_confidence = float(game_signal.get("confidence") or 50)
    reasons: List[str] = []

    if game_signal.get("multi_book_agreement"):
        base_confidence += 6
        reasons.append("multi-book agreement")
    if game_signal.get("moneyline_steam"):
        base_confidence += 5
        reasons.append("moneyline steam")
    if game_signal.get("spread_confirmation"):
        base_confidence += 4
        reasons.append("spread confirmation")
    if game_signal.get("total_confirmation"):
        base_confidence += 2
        reasons.append("total confirmation")
    if game_signal.get("dog_tightening"):
        base_confidence += 3
        reasons.append("dog tightening")
    if game_signal.get("favorite_not_separating"):
        base_confidence -= 4
        reasons.append("favorite not separating")
    if game_signal.get("spread_disagreement"):
        base_confidence -= 5
        reasons.append("spread disagreement")
    if game_signal.get("late_reversal"):
        base_confidence -= 10
        reasons.append("late reversal")

    confidence = max(0.0, min(95.0, base_confidence))
    label = _confidence_label(confidence)
    publish_threshold = SPORT_CONFIG[sport]["min_publish_confidence"]

    if confidence >= publish_threshold and team:
        status = "PUBLISHED"
    elif confidence >= 58 and team:
        status = "WATCHLIST"
    else:
        status = "NO_EDGE"
        team = None

    return WinnerPrediction(
        sport=sport,
        game_key=game_key,
        predicted_team=team,
        prediction_status=status,
        confidence=round(confidence, 2),
        confidence_label=label,
        reason_codes=reasons,
        user_message=_build_user_message(team, status, label, reasons),
    )


def winner_model_requirements(sport: str) -> Dict[str, Any]:
    sport = sport.lower()
    if sport not in SPORT_CONFIG:
        raise ValueError(f"Unsupported sport: {sport}")
    return {"sport": sport, **SPORT_CONFIG[sport]}


def all_sport_winner_requirements() -> Dict[str, Any]:
    return {
        "ok": True,
        "objective": "65%+ accuracy on published individual-game winner predictions by sport",
        "important_rule": "Do not force picks. Analyze every game, publish only when confidence gate is met.",
        "parlays": "Excluded from this model. Parlays stay in separate sport-specific engines.",
        "sports": SPORT_CONFIG,
    }
