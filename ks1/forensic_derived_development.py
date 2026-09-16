"""Run derived-forensic development selection without touching the frozen holdout.

This staging entry point intentionally stops after purged development selection until
the shared serving transform is wired into KS1 daily inference.  It reserves the exact
300-game holdout identities but never scores them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from ks1.development import HOLDOUT, frozen_split
from ks1.forensic_derived_selected_baseline import CONTRACT, development_select
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
    selected, development = development_select(train)
    (output/"development_selection.json").write_bytes(encode(development))
    report = {
        "contract": CONTRACT,
        "accepted": False,
        "qualification_run": False,
        "reason": (development.get("reason") if selected is None
                   else "derived_candidate_development_passed_serving_adapter_pending"),
        "development_candidate_selected": selected is not None,
        "reserved_holdout_games": len(holdout),
        "holdout_predictions_generated": 0,
        "final_holdout_used_for_selection": False,
        "training_population": population,
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
        "holdout_predictions_generated": report["holdout_predictions_generated"],
    }, indent=2))


if __name__ == "__main__":
    main()
