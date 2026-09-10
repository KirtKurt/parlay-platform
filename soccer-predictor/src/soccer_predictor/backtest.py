import pandas as pd
import numpy as np
from .io import day
from .state import build_features
from .model import Model
from .predict import btts_label
from .odds import bookmaker


def validate_ledger(ledger, refit_every=7):
    if ledger.empty:
        raise ValueError("Backtest produced no predictions")
    if ledger.match_id.duplicated().any():
        raise ValueError("Duplicate prediction IDs")
    for r in ledger.to_dict("records"):
        d, cutoff = day(r["date"]), day(r["fit_cutoff"])
        if not day(r["train_max_date"]) < cutoff <= d:
            raise ValueError("Training cutoff leakage")
        if (d-cutoff).days >= refit_every:
            raise ValueError("Refit cadence violated")
        if r.get("feature_asof") and day(r["feature_asof"]) >= d:
            raise ValueError("Feature leakage")
        for prefix in ["", "ml_", "dc_", "blend_"]:
            p = np.array([r[prefix+k] for k in ["p_home", "p_draw", "p_away"]])
            if not np.isfinite(p).all() or np.any(p < 0) or not np.isclose(p.sum(), 1):
                raise ValueError("Invalid outcome probabilities")
    return {"passed": True, "rows": len(ledger), "unique_matches": ledger.match_id.nunique(),
            "checks": ["strict pre-match features", "pre-cutoff labels", "refit cadence", "unique matches", "probability sums"]}


def walk_forward(results, start, end, refit_every=7, priors=None, min_rows=100):
    start, end = day(start), day(end)
    if start >= end or not 1 <= int(refit_every) <= 31:
        raise ValueError("Require start < exclusive end and refit interval 1..31 days")
    features, _ = build_features(results[results.date < end], priors)
    eligible = features[(features.date >= start) & (features.date < end)]
    ledger, active_cutoff, model = [], None, None
    for d, group in eligible.groupby("date", sort=True):
        cutoff = start + pd.Timedelta(days=((day(d)-start).days//refit_every)*refit_every)
        if active_cutoff is None or cutoff != active_cutoff:
            model = Model().fit(features, cutoff, min_rows=min_rows)
            active_cutoff = cutoff
        for r in group.to_dict("records"):
            prediction = model.predict(r)
            entry = {"match_id": r["match_id"], "date": day(r["date"]).isoformat(),
                     "div": r["div"], "season": r["season"], "home": r["home"], "away": r["away"],
                     "hg": int(r["hg"]), "ag": int(r["ag"]), "y": int(r["y"]),
                     "y_ou25": int(r["hg"]+r["ag"] > 2.5),
                     "y_btts": int(btts_label(r["hg"], r["ag"])), **prediction}
            entry.update(bookmaker(r, prediction))
            ledger.append(entry)
    frame = pd.DataFrame(ledger)
    validate_ledger(frame, refit_every)
    return frame
