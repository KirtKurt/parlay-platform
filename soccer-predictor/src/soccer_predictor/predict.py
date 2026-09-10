import numpy as np
from .dixon_coles import outcome_probs


def btts_label(home_goals, away_goals):
    return bool(home_goals > 0 and away_goals > 0)


def double_chance(p):
    h, d, a = p
    values = np.array([h+d, h+a, d+a])
    index = int(np.argmax(values))
    return ("1X", "12", "X2")[index], float(values[index])


def markets(grid):
    if grid.ndim != 2 or grid.shape[0] != grid.shape[1] or np.any(grid < 0) or not np.isclose(grid.sum(), 1):
        raise ValueError("Invalid score distribution")
    p = outcome_probs(grid)
    i, j = np.indices(grid.shape)
    h, a = np.unravel_index(int(np.argmax(grid)), grid.shape)
    btts = float(grid[(i > 0) & (j > 0)].sum())
    dc, dc_p = double_chance(p)
    out = {"p_home": float(p[0]), "p_draw": float(p[1]), "p_away": float(p[2]),
           "pick": ("H", "D", "A")[int(np.argmax(p))], "pick_probability": float(np.max(p)),
           "score": f"{h}-{a}", "p_score": float(grid[h, a]),
           "p_btts_yes": btts, "p_btts_no": 1-btts, "btts_pick": "Yes" if btts >= .5 else "No",
           "p_1x": float(p[0]+p[1]), "p_12": float(p[0]+p[2]), "p_x2": float(p[1]+p[2]),
           "double_chance_pick": dc, "double_chance_probability": dc_p}
    for n in [15, 25, 35]:
        over = float(grid[i+j > n/10].sum())
        out[f"p_over{n}"] = over
        out[f"p_under{n}"] = 1-over
    return out


def confidence(probability):
    return "High" if probability >= .58 else "Medium" if probability >= .46 else "Low"
