"""Failure taxonomy. One loss is not a retrain trigger."""
from __future__ import annotations


def classify(row: dict) -> str:
    y = int(row["home_win"])
    p = float(row["p_lgb"])
    predicted = int(p >= 0.5)
    if predicted == y:
        return "correct"
    gap = abs(p - 0.5)
    poisson = row.get("p_poisson") or row.get("p_home_poisson")
    market = row.get("p_market") or row.get("market_home_prob")
    if row.get("starter_unverified") or row.get("lineup_status") not in (None, "confirmed"):
        return "lineup_or_starter_uncertainty"
    if poisson is not None and (
        ((float(poisson) >= 0.5) != bool(predicted)) or abs(p - float(poisson)) > 0.08
    ):
        return "engine_disagreement"
    if market is not None and abs(p - float(market)) > 0.08:
        return "model_vs_market_fight"
    if gap < 0.05:
        return "normal_randomness_coin_flip"
    if 0.70 <= p or p <= 0.30:
        return "confident_miss_review_calibration"
    return "undifferentiated_miss"


def summarize(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        label = classify(row)
        counts[label] = counts.get(label, 0) + 1
    return {
        "games": len(rows),
        "counts": counts,
        "rule": "Do not refit on a single miss. Promote a hypothesis only after a repeated bucket.",
    }
