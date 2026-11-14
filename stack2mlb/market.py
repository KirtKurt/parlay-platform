"""Two-way vig removal and American-odds conversion."""
from __future__ import annotations


def american_to_implied(odds: float) -> float:
    if odds is None:
        raise ValueError("odds required")
    odds = float(odds)
    if odds == 0:
        raise ValueError("american odds cannot be 0")
    if odds < 0:
        return (-odds) / ((-odds) + 100.0)
    return 100.0 / (odds + 100.0)


def devig_two_way(p_home_raw: float, p_away_raw: float | None = None) -> float:
    """Proportional (multiplicative) de-vig. Returns fair P(home)."""
    h = float(p_home_raw)
    a = float(1.0 - h) if p_away_raw is None else float(p_away_raw)
    if h <= 0 or a <= 0:
        raise ValueError("implied probabilities must be positive")
    total = h + a
    fair = h / total
    if not 0.0 < fair < 1.0:
        raise ValueError("de-vig produced an invalid probability")
    return fair


def fair_from_american(home_odds: float, away_odds: float) -> float:
    return devig_two_way(american_to_implied(home_odds), american_to_implied(away_odds))


def clip01(p: float, eps: float = 1e-6) -> float:
    return min(1.0 - eps, max(eps, float(p)))
