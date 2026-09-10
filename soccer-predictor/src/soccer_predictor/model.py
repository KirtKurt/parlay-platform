import numpy as np
import pandas as pd
from .settings import FEATURES, MODEL_FEATURES, FEATURE_DEFAULTS, SEED
from .io import day, stamp, sha, json_bytes
from .ml import MultinomialLogit
from .dixon_coles import DixonColes, outcome_probs
from .blend import blend_grid
from .predict import markets, confidence


class Model:
    def __init__(self):
        self.ml, self.dc = MultinomialLogit(), DixonColes()
        self.meta = {}

    def fit(self, features, cutoff, min_rows=100):
        cutoff = day(cutoff)
        start = cutoff - pd.DateOffset(years=4)
        train = features[(features.date >= start) & (features.date < cutoff)].copy()
        train = train.sort_values(["date", "div", "home", "away"], kind="stable")
        if len(train) < min_rows:
            raise ValueError(f"Need >= {min_rows} preceding training rows; found {len(train)}")
        for r in train.to_dict("records"):
            if r.get("feature_asof") and day(r["feature_asof"]) >= day(r["date"]):
                raise ValueError("Leaking training features")
        x = self._matrix(train, MODEL_FEATURES)
        self.ml.fit(x, train.y.to_numpy(int))
        self.dc.fit(train)
        self.meta = {"fit_cutoff": stamp(cutoff), "train_window_start": stamp(start),
                     "train_min_date": stamp(train.date.min()), "train_max_date": stamp(train.date.max()),
                     "training_rows": len(train), "features": MODEL_FEATURES, "seed": SEED,
                     "class_order": ["H", "D", "A"], "window_years": 4}
        self.meta["model_id"] = sha(json_bytes(self.to_dict(include_id=False)))
        return self

    def predict(self, row):
        d = day(row["date"])
        if day(self.meta["fit_cutoff"]) > d or day(self.meta["train_max_date"]) >= d:
            raise ValueError("Model was fit using same-day or future labels")
        if row.get("feature_asof") and day(row["feature_asof"]) >= d:
            raise ValueError("Same-day/future feature state")
        feature_names = self.meta.get("features", FEATURES)
        ml = self.ml.predict_proba(self._row_matrix(row, feature_names))[0]
        grid, audit = self.dc.predict(row["div"], row["home"], row["away"])
        dc = outcome_probs(grid)
        blended, weight = blend_grid(grid, ml, row["elo_gap"])
        result, dc_markets = markets(blended), markets(grid)
        result.update({**audit, "ml_weight": weight, "dc_weight": 1-weight,
                       "model_id": self.meta["model_id"], "fit_cutoff": self.meta["fit_cutoff"],
                       "train_max_date": self.meta["train_max_date"],
                       "train_window_start": self.meta["train_window_start"],
                       "training_rows": self.meta["training_rows"], "feature_asof": row.get("feature_asof")})
        for prefix, probs in [("ml", ml), ("dc", dc), ("blend", outcome_probs(blended))]:
            for key, value in zip(["p_home", "p_draw", "p_away"], probs):
                result[f"{prefix}_{key}"] = float(value)
        result["dc_p_over25"], result["dc_p_btts_yes"] = dc_markets["p_over25"], dc_markets["p_btts_yes"]
        notes = list(audit["dc_flags"])
        if row.get("home_n8", 0) < 8 or row.get("away_n8", 0) < 8:
            notes.append("Sparse rolling team history")
        if max(result["p_home"], result["p_away"]) >= .70:
            notes.append("Mismatch: favorite probability >= 70%")
        result["confidence_tier"] = confidence(result["pick_probability"])
        result["confidence_notes"] = "; ".join(notes)
        for k in list(dict.fromkeys(FEATURES + MODEL_FEATURES + ["home_elo", "away_elo", "home_n8", "away_n8", "market_feature_safe"])):
            result[k] = row.get(k)
        return result

    @staticmethod
    def _coerce_feature(name, value, market_safe=True):
        if name.startswith("mkt_") and not market_safe:
            return float(FEATURE_DEFAULTS[name])
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = float("nan")
        if not np.isfinite(value):
            if name not in FEATURE_DEFAULTS:
                raise ValueError(f"Missing required feature: {name}")
            return float(FEATURE_DEFAULTS[name])
        return value

    @classmethod
    def _row_matrix(cls, row, names):
        safe = bool(row.get("market_feature_safe", True))
        return np.asarray([[cls._coerce_feature(name, row.get(name), safe) for name in names]], dtype=float)

    @classmethod
    def _matrix(cls, frame, names):
        return np.asarray([[cls._coerce_feature(name, row.get(name), bool(row.get("market_feature_safe", True))) for name in names] for row in frame.to_dict("records")], dtype=float)

    def to_dict(self, include_id=True):
        meta = dict(self.meta)
        if not include_id:
            meta.pop("model_id", None)
        return {"meta": meta, "ml": self.ml.to_dict(), "dc": self.dc.to_dict()}

    @classmethod
    def from_dict(cls, value):
        obj = cls()
        obj.meta, obj.ml, obj.dc = value["meta"], MultinomialLogit.from_dict(value["ml"]), DixonColes.from_dict(value["dc"])
        if obj.meta.get("features") not in (FEATURES, MODEL_FEATURES):
            raise ValueError("Feature schema mismatch")
        if sha(json_bytes(obj.to_dict(include_id=False))) != obj.meta["model_id"]:
            raise ValueError("Model checksum mismatch")
        return obj
