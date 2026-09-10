import math


def goal_multiplier(goal_difference: int) -> float:
    gd = abs(int(goal_difference))
    return 1.0 if gd <= 1 else 1.5 if gd == 2 else (11.0 + gd) / 8.0


def update(home: float, away: float, home_goals: int, away_goals: int):
    expectation = 1.0 / (1.0 + 10.0 ** ((away - home - 55.0) / 400.0))
    observed = 1.0 if home_goals > away_goals else 0.0 if home_goals < away_goals else 0.5
    delta = 22.0 * goal_multiplier(home_goals - away_goals) * (observed - expectation)
    return home + delta, away - delta
