#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import pandas as pd
from _common import ROOT, fail_main
from soccer_predictor.io import TeamNames, write_json, atomic_bytes
from soccer_predictor.fixtures import fetch_fixtures
from soccer_predictor.service import predict_bundle
from soccer_predictor.report import prediction_card


def main():
    parser = argparse.ArgumentParser(description="Predict real upcoming fixtures; print UCL + Premier League card")
    parser.add_argument("--from", dest="first", required=True)
    parser.add_argument("--to", dest="last", required=True, help="Inclusive date")
    parser.add_argument("--out", default=str(ROOT/"artifacts/predictions.csv"))
    parser.add_argument("--model", default=str(ROOT/"artifacts/model.json"))
    parser.add_argument("--fixtures", help="Existing verified fixture envelope; default fetches fresh feeds")
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text())
    fixtures = json.loads(Path(args.fixtures).read_text()) if args.fixtures else fetch_fixtures(args.first, args.last, TeamNames(model["team_names"]))
    output = predict_bundle(model, fixtures, args.first, args.last)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(output["predictions"]).to_csv(out.with_suffix(".csv"), index=False)
    write_json(out.with_suffix(".json"), output)
    card = prediction_card(output["predictions"])
    atomic_bytes(out.with_suffix(".md"), (card+"\n").encode())
    print(card)
    print(f"\nSaved {out.with_suffix('.csv')} and {out.with_suffix('.json')}")

if __name__ == "__main__":
    fail_main(main)
