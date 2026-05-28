from typing import Any, Dict, List


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _confidence_label(confidence: float, volatility_flags: List[str]) -> str:
    if volatility_flags:
        return "volatile"
    if confidence >= 75:
        return "strong"
    if confidence >= 62:
        return "moderate"
    return "watchlist"


def build_hot_side_narrative(prediction: Dict[str, Any]) -> Dict[str, Any]:
    """Create user-facing language for hot-side predictions.

    The frontend should display these fields directly so users see a clear
    narrative rather than raw deltas only.
    """
    team = prediction.get("predicted_team") or "Unknown team"
    market = (prediction.get("market") or "moneyline").lower()
    confidence = _as_float(prediction.get("confidence"), 50.0)
    reasons: List[str] = []
    volatility_flags: List[str] = []

    home_delta = _as_float(prediction.get("home_delta"))
    away_delta = _as_float(prediction.get("away_delta"))

    if abs(home_delta) > 0 or abs(away_delta) > 0:
        reasons.append("moneyline movement")

    if prediction.get("favorite_not_separating"):
        reasons.append("favorite not separating")
        volatility_flags.append("favorite not separating")

    if prediction.get("dog_tightening"):
        reasons.append("dog tightening")

    if prediction.get("spread_disagreement"):
        reasons.append("spread disagreement")
        volatility_flags.append("spread disagreement")

    if prediction.get("spread_confirmation"):
        reasons.append("spread confirmation")

    if prediction.get("total_confirmation"):
        reasons.append("total confirmation")

    if not reasons:
        reasons.append("market movement detected")

    label = _confidence_label(confidence, volatility_flags)

    if label == "volatile":
        prediction_text = f"{team} upset/watchlist"
        hot_side = f"{team} pressure"
    elif market in {"spread", "spreads"}:
        prediction_text = f"{team} spread lean"
        hot_side = f"{team} spread pressure"
    elif market in {"total", "totals", "over_under", "ou"}:
        side = prediction.get("predicted_total_side") or prediction.get("predicted_side") or "total"
        prediction_text = f"{str(side).title()} lean"
        hot_side = f"{str(side).title()} pressure"
    else:
        prediction_text = f"{team} moneyline lean"
        hot_side = f"{team} pressure"

    reason_text = " + ".join(reasons)
    return {
        "hot_side": hot_side,
        "prediction": prediction_text,
        "reason": reason_text,
        "confidence_label": label,
        "confidence_note": f"Confidence: {label}",
        "display_text": {
            "hot_side": f"Hot Side: {hot_side}",
            "prediction": f"Prediction: {prediction_text}",
            "reason": f"Reason: {reason_text}",
            "confidence": f"Confidence: {label}",
        },
    }


def red_sox_style_example() -> Dict[str, str]:
    return {
        "hot_side": "Hot Side: Red Sox pressure",
        "prediction": "Prediction: Red Sox upset/watchlist",
        "reason": "Reason: favorite not separating + dog tightening + spread disagreement",
        "confidence": "Confidence: volatile",
    }
