from collections import deque
import numpy as np
import pandas as pd
from .elo import update
from .io import day, stamp


class State:
    def __init__(self, priors=None):
        self.priors = priors or {"default_elo": 1500, "teams": {}}
        if self.priors.get("default_elo", 1500) != 1500 or any(v != 1500 for v in self.priors.get("teams", {}).values()):
            raise ValueError("v1 requires neutral 1500 priors to prevent historical hindsight")
        self.ratings = {}
        self.history = {}
        self.last_date = None

    def rating(self, name):
        return self.ratings.get(name, self.priors.get("teams", {}).get(name, 1500.0))

    def form(self, name, asof):
        start = day(asof) - pd.DateOffset(years=4)
        rows = [r for r in self.history.get(name, []) if day(r["date"]) >= start]
        if not rows:
            return {"gf8": 1.35, "ga8": 1.35, "pts5": 1.0, "n8": 0, "last": None}
        return {"gf8": float(np.mean([r["gf"] for r in rows])),
                "ga8": float(np.mean([r["ga"] for r in rows])),
                "pts5": float(np.mean([r["points"] for r in rows[-5:]])),
                "n8": len(rows), "last": rows[-1]["date"]}

    def features(self, home, away, date):
        d = day(date)
        if self.last_date is not None and day(self.last_date) >= d:
            raise ValueError("Feature state contains same-day or future results")
        h, a = self.form(home, d), self.form(away, d)
        eh, ea = self.rating(home), self.rating(away)
        out = {"elo_gap": eh + 55 - ea, "raw_elo_gap": eh - ea,
               "home_elo": eh, "away_elo": ea, "feature_asof": self.last_date}
        for prefix, values in [("home", h), ("away", a)]:
            out.update({f"{prefix}_{k}": v for k, v in values.items()})
        return out

    def update_day(self, rows):
        rows = list(rows)
        if not rows:
            return
        d = day(rows[0]["date"])
        if any(day(r["date"]) != d for r in rows) or (self.last_date and day(self.last_date) >= d):
            raise ValueError("Results must be updated in strictly increasing date batches")
        seen = set()
        for r in rows:
            h, a, hg, ag = r["home"], r["away"], int(r["hg"]), int(r["ag"])
            if h in seen or a in seen:
                raise ValueError("Repeated club within one date batch")
            seen.update([h, a])
            self.ratings[h], self.ratings[a] = update(self.rating(h), self.rating(a), hg, ag)
            for team, gf, ga in [(h, hg, ag), (a, ag, hg)]:
                history = self.history.setdefault(team, deque(maxlen=8))
                history.append({"date": stamp(d), "gf": gf, "ga": ga,
                                "points": 3 if gf > ga else 1 if gf == ga else 0})
        self.last_date = stamp(d)

    def to_dict(self):
        return {"priors": self.priors, "ratings": self.ratings,
                "history": {k: list(v) for k, v in self.history.items()}, "last_date": self.last_date}

    @classmethod
    def from_dict(cls, value):
        s = cls(value["priors"])
        s.ratings = {k: float(v) for k, v in value["ratings"].items()}
        s.history = {k: deque(v, maxlen=8) for k, v in value["history"].items()}
        s.last_date = value["last_date"]
        return s


def build_features(results, priors=None):
    state, out = State(priors), []
    ordered = results.sort_values(["date", "div", "home", "away"], kind="stable")
    for _, group in ordered.groupby("date", sort=True):
        rows = group.to_dict("records")
        for row in rows:
            out.append({**row, **state.features(row["home"], row["away"], row["date"])})
        state.update_day(rows)
    return pd.DataFrame(out), state


def state_before(results, cutoff, priors=None):
    return build_features(results[results["date"] < day(cutoff)], priors)[1]
