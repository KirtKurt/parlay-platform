"""Run derived-forensic development selection without touching the frozen holdout.

The exact 300-game qualification holdout is separated first and never scored here.
For the remaining qualified development population, exact versioned pre-T10 MLB feeds
may recover live-equivalent lineup season values only after source identity/hash checks.
The candidate still stops after purged development selection until the same transform is
wired into live serving and final qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from ks1.development import HOLDOUT, frozen_split
from ks1.forensic_derived_stable_selection import CONTRACT, development_select
from ks1.historical_lineup_season_probe import enrich_frame
from ks1.inventory import encode
from ks1.retrain_recent import qualified_training_population
from ks1.sources import aws_clients
from ks1.train import save_artifact


def run(input_path, proof_path, output):
    output.mkdir(parents=True, exist_ok=True)
    proof = json.loads(Path(proof_path).read_bytes())
    body = Path(input_path).read_bytes()
    if hashlib.sha256(body).hexdigest() != proof["input_table_sha256"]:
        raise ValueError("input table checksum mismatch")
    frame = pd.read_parquet(input_path)
    train, holdout = frozen_split(frame, json.loads(HOLDOUT.read_bytes()))
    train, population = qualified_training_population(train, proof.get("source_receipts", []))

    # Holdout separation is deliberately above this read. Only the qualified
    # pre-holdout population can cause a retained source object to be fetched.
    _, s3, _ = aws_clients("us-east-1", "parlay-platform-dev")
    train, lineup_source_enrichment = enrich_frame(train, s3)
    selected, development = development_select(train)
    development["direct_lineup_source_enrichment"] = lineup_source_enrichment
    (output/"development_selection.json").write_bytes(encode(development))
    report = {
        "contract": CONTRACT,
        "accepted": False,
        "qualification_run": False,
        "reason": (development.get("reason") if selected is None
                   else "derived_candidate_development_passed_serving_adapter_pending"),
        "development_candidate_selected": selected is not None,
        "reserved_holdout_games": len(holdout),
        "holdout_source_rows_inspected": 0,
        "holdout_predictions_generated": 0,
        "final_holdout_used_for_selection": False,
        "training_population": population,
        "direct_lineup_source_enrichment": lineup_source_enrichment,
        "development": development,
        "prediction_writes": 0,
        "official_ledger_writes": 0,
        "input_table_sha256": proof["input_table_sha256"],
    }
    (output/"metrics.json").write_bytes(encode(report))
    (output/"input_proof.json").write_bytes(encode(proof))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = run(args.input, args.proof, args.output)
    _, s3, bucket = aws_clients("us-east-1", "parlay-platform-dev")
    save_artifact(s3, bucket, args.output)
    print(json.dumps({
        "development_candidate_selected": report["development_candidate_selected"],
        "reason": report["reason"],
        "reserved_holdout_games": report["reserved_holdout_games"],
        "holdout_source_rows_inspected": report["holdout_source_rows_inspected"],
        "holdout_predictions_generated": report["holdout_predictions_generated"],
        "direct_lineup_top4_floor_reached": report[
            "direct_lineup_source_enrichment"]["top4_minimum_reached"],
    }, indent=2))


if __name__ == "__main__":
    main()
