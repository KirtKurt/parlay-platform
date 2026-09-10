import numpy as np
from .dixon_coles import outcome_probs


def ml_weight(elo_gap):
    gap = abs(float(elo_gap))
    return .28 if gap >= 200 else .40 if gap >= 120 else .50


def blend_grid(grid, ml_probs, elo_gap):
    dc = outcome_probs(grid)
    ml = np.asarray(ml_probs, float)
    if ml.shape != (3,) or np.any(ml < 0) or not np.isclose(ml.sum(), 1):
        raise ValueError("Expected normalized H/D/A probabilities")
    weight = ml_weight(elo_gap)
    target = weight*ml + (1-weight)*dc
    i, j = np.indices(grid.shape)
    result = grid.copy()
    for k, mask in enumerate([i > j, i == j, i < j]):
        if dc[k] <= 0:
            raise ValueError("DC outcome has zero probability")
        result[mask] *= target[k]/dc[k]
    return result/result.sum(), weight
