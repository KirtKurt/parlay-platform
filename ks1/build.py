"""Build and verify Phase 1 from existing data; optional main-pipeline publication."""
import argparse
from datetime import date
import json
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc

from ks1.inventory import encode
from ks1.publish import parquet_bytes, publish
from ks1.sources import aws_clients, load_existing
from ks1.table import build


def run(args):
    cf, s3, bucket = aws_clients(args.region, args.stack)
    bundle = load_existing(cf, s3, bucket)
    table, report, dictionary, sample, crosswalk = build(bundle, args.date)
    # Rebuild one date independently from the same retained input snapshot.
    selected = max(table["date"].to_pylist())
    again, *_ = build(bundle, selected)
    if not again.equals(table.filter(pc.equal(table["date"], selected))):
        raise ValueError("independent date rebuild changed rows")
    report["date_rebuild_verified"] = selected
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    (output / "game_table.parquet").write_bytes(parquet_bytes(table))
    pd.DataFrame(dictionary).to_csv(output / "column_sources.csv", index=False)
    sample.to_csv(output / "sample_20.csv", index=False)
    (output / "crosswalk.json").write_bytes(encode(crosswalk))
    (output / "report.json").write_bytes(encode(report))
    if args.publish:
        deployment = publish(s3, bucket, table, report["source_receipts"])
        second = publish(s3, bucket, again, report["source_receipts"])
        if second["write_keys"]:
            raise ValueError("same-input rerun unexpectedly wrote objects")
        deployment["same_date_noop_verified"] = selected
        deployment["second_run_write_count"] = len(second["write_keys"])
        (output / "deployment.json").write_bytes(encode(deployment))
    compact = {k: v for k, v in report.items() if k not in ("source_receipts", "gaps")}
    print(json.dumps(compact, indent=2))
    print(sample[["game_id", "date", "home_team", "away_team", "home_score", "away_score",
                  "home_starter_name", "away_starter_name"]].to_string(index=False))
    if args.publish:
        print(json.dumps({"deployment": "verified", "partitions": len(deployment["partitions"]),
                          "same_date_noop_verified": selected, "bucket": bucket, "prefix": deployment["prefix"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date", type=lambda value: date.fromisoformat(value).isoformat())
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="parlay-platform-dev")
    parser.add_argument("--publish", action="store_true")
    run(parser.parse_args())
