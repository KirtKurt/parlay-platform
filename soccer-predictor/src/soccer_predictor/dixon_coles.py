import math
import numpy as np


def score_matrix(lam: float, mu: float, rho=-0.13, max_goals=24):
    if not (0 < lam <= 5.5 and 0 < mu <= 5.5):
        raise ValueError("Goal rates must be in (0, 5.5]")
    h, a = np.empty(max_goals + 1), np.empty(max_goals + 1)
    h[0], a[0] = math.exp(-lam), math.exp(-mu)
    for i in range(1, max_goals + 1):
        h[i], a[i] = h[i-1] * lam / i, a[i-1] * mu / i
    grid = np.outer(h, a)
    grid[0, 0] *= 1 - lam * mu * rho
    grid[0, 1] *= 1 + lam * rho
    grid[1, 0] *= 1 + mu * rho
    grid[1, 1] *= 1 - rho
    if np.any(grid < 0):
        raise ValueError("Invalid Dixon-Coles correction")
    return grid / grid.sum()


def outcome_probs(grid):
    return np.array([np.tril(grid, -1).sum(), np.trace(grid), np.triu(grid, 1).sum()])


class DixonColes:
    def __init__(self, rho=-0.13):
        self.rho = float(rho)
        self.pooled = [1.5, 1.2]
        self.leagues, self.strengths = {}, {}

    def fit(self, rows):
        if len(rows) == 0:
            raise ValueError("Empty DC training history")
        self.pooled = [float(rows.hg.mean()), float(rows.ag.mean())]
        self.leagues, self.strengths = {}, {}
        for div, group in rows.groupby("div"):
            n = len(group)
            self.leagues[div] = [(float(group.hg.sum()) + 30*self.pooled[0])/(n+30),
                                 (float(group.ag.sum()) + 30*self.pooled[1])/(n+30)]
        history = {}
        for r in rows.sort_values(["date", "div", "home"], kind="stable").to_dict("records"):
            for t, gf, ga in [(r["home"], r["hg"], r["ag"]), (r["away"], r["ag"], r["hg"])]:
                history.setdefault(t, []).append((float(gf), float(ga), r["div"]))
        for team, games in history.items():
            games = games[-8:]
            base = max(.1, float(np.mean(self.leagues[games[-1][2]])))
            denom = (len(games) + 3) * base
            self.strengths[team] = [(sum(g[0] for g in games) + 3*base)/denom,
                                    (sum(g[1] for g in games) + 3*base)/denom, len(games)]
        return self

    def predict(self, div, home, away):
        baseline = self.leagues.get(div, self.pooled)
        h, a = self.strengths.get(home, [1, 1, 0]), self.strengths.get(away, [1, 1, 0])
        raw_lam, raw_mu = baseline[0]*h[0]*a[1], baseline[1]*a[0]*h[1]
        lam, mu = float(np.clip(raw_lam, .05, 5.5)), float(np.clip(raw_mu, .05, 5.5))
        flags = []
        if div not in self.leagues:
            flags.append("Competition not trained; pooled domestic baseline / cross-league extrapolation")
        if h[2] < 8 or a[2] < 8:
            flags.append("Sparse DC form or neutral prior")
        if lam != raw_lam or mu != raw_mu:
            flags.append("Goal rate clipped for valid fixed-rho probabilities")
        return score_matrix(lam, mu, self.rho), {"lambda_home": lam, "lambda_away": mu, "dc_flags": flags}

    def to_dict(self):
        return {"rho": self.rho, "pooled": self.pooled, "leagues": self.leagues, "strengths": self.strengths}

    @classmethod
    def from_dict(cls, value):
        out = cls(value["rho"])
        out.pooled, out.leagues, out.strengths = value["pooled"], value["leagues"], value["strengths"]
        return out
