#!/usr/bin/env python3
import argparse
import importlib.util
import os
from pathlib import Path
from _common import ROOT, fail_main
from soccer_predictor.io import TeamNames, read_config, load_history, write_json
from soccer_predictor.backtest import walk_forward, validate_ledger
from soccer_predictor.odds_api import attach_historical_market
from soccer_predictor.report import write_reports, markdown_metrics


def main():
    parser = argparse.ArgumentParser(description="Genuine rolling-origin four-year-window backtest; never shuffle")
    parser.add_argument("--start", default="2018-08-01")
    parser.add_argument("--end", default="2026-09-07", help="Exclusive end date")
    parser.add_argument("--refit-every", type=int, default=7)
    parser.add_argument("--raw", default=str(ROOT/"data/raw"))
    parser.add_argument("--out", default=str(ROOT/"artifacts"))
    parser.add_argument("--allow-partial-history", action="store_true", help="Diagnostic only; cannot become a production model")
    args = parser.parse_args()
    if importlib.util.find_spec("pyarrow") is None:
        raise RuntimeError("Parquet storage requires the optional extra: python -m pip install -e '.[backtest]'")
    names = TeamNames.load(ROOT/"config/teams.yaml")
    results, provenance = load_history(args.raw, names, args.allow_partial_history)
    if os.getenv("ODDS_API_KEY"):
        print("Odds API key detected: enriching EPL rows from 2020-06-06 using one cached 12:00 UTC snapshot per match-date.")
        results = attach_historical_market(results, names, ROOT/"data/odds_cache")
        provenance["odds_api_historical"] = "enabled_cached_daily_noon_utc"
    else:
        provenance["odds_api_historical"] = "skipped_no_key; football-data bookmaker columns used"
    ledger = walk_forward(results, args.start, args.end, args.refit_every, read_config(ROOT/"config/priors.yaml"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger.to_parquet(out/"backtest.parquet", engine="pyarrow", compression="snappy", index=False)
    metrics = write_reports(ledger, out, "backtest", provenance)
    write_json(out/"backtest_integrity.json", validate_ledger(ledger, args.refit_every))
    print(markdown_metrics(metrics))
    if not provenance["partial_history"] and args.start == "2018-08-01" and args.end == "2026-09-07" and args.refit_every == 7:
        path = ROOT/"README.md"
        if path.exists():
            text = path.read_text()
            begin, end = "<!-- BACKTEST_RESULTS_START -->", "<!-- BACKTEST_RESULTS_END -->"
            if begin in text and end in text:
                before, rest = text.split(begin, 1)
                _, after = rest.split(end, 1)
                path.write_text(before+begin+"\n\nCOMPLETED: verified real-data run.\n\n"+markdown_metrics(metrics)+"\n\n"+end+after)

if __name__ == "__main__":
    fail_main(main)
