#!/usr/bin/env python3
import argparse
import pickle
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from _common import ROOT, fail_main
from soccer_predictor.io import day, TeamNames, read_config, load_history, write_json
from soccer_predictor.state import build_features
from soccer_predictor.model import Model
from soccer_predictor.backtest import walk_forward
from soccer_predictor.report import write_reports, markdown_metrics


def main():
    parser = argparse.ArgumentParser(description="Fit on strictly pre-cutoff matches; report walk-forward holdout")
    parser.add_argument("--cutoff", required=True, help="Exclusive UTC label cutoff")
    parser.add_argument("--raw", default=str(ROOT/"data/raw"))
    parser.add_argument("--out", default=str(ROOT/"artifacts/model.json"))
    parser.add_argument("--holdout-days", type=int, default=180)
    parser.add_argument("--allow-partial-history", action="store_true", help="Testing only; production publication will refuse this model")
    args = parser.parse_args()
    if args.holdout_days < 1:
        raise ValueError("Holdout days must be positive; evaluation cannot be disabled")
    cutoff = day(args.cutoff)
    if cutoff > day(datetime.now(timezone.utc)):
        raise ValueError("Training cutoff cannot be in the future")
    names = TeamNames.load(ROOT/"config/teams.yaml")
    priors = read_config(ROOT/"config/priors.yaml")
    results, provenance = load_history(args.raw, names, args.allow_partial_history)
    prior = results[results.date < cutoff]
    holdout = walk_forward(prior, cutoff-pd.Timedelta(days=args.holdout_days), cutoff, 7, priors)
    out = Path(args.out)
    metrics = write_reports(holdout, out.parent, "holdout", provenance)
    holdout.to_csv(out.parent/"holdout_predictions.csv", index=False)
    features, state = build_features(prior, priors)
    model = Model().fit(features, cutoff)
    bundle = {"schema": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "model": model.to_dict(), "state": state.to_dict(), "team_names": names.mapping,
              "provenance": provenance,
              "holdout": {"method": "rolling origin; past-only independent fits", "rows": len(holdout),
                          "start": (cutoff-pd.Timedelta(days=args.holdout_days)).isoformat(), "end_exclusive": cutoff.isoformat()}}
    write_json(out, bundle)
    models = ROOT/"models"
    models.mkdir(parents=True, exist_ok=True)
    with open(models/"logistic.pkl", "wb") as fh:
        pickle.dump(model.ml, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with open(models/"elo.pkl", "wb") as fh:
        pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
    with open(models/"dixon_coles.pkl", "wb") as fh:
        pickle.dump(model.dc, fh, protocol=pickle.HIGHEST_PROTOCOL)
    write_json(models/"bundle.json", bundle)
    print(markdown_metrics(metrics[metrics.season == "POOLED"]))
    print(f"Saved {out}; final training rows: {model.meta['training_rows']}")

if __name__ == "__main__":
    fail_main(main)
