#!/usr/bin/env python3
"""Freeze and grade the best learned MLB V8 challenger observationally.

This runner uses a separate pointer and artifact namespace from the guarded
promotion prospective audit.  Its output is descriptive shadow evidence only.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import mlb_v8_observational_audit_v1 as observational
import run_mlb_v8_prospective_audit as prospective_runner


VERSION = "MLB-V8-OBSERVATIONAL-AUDIT-RUNNER-v1-exact-controller-artifact"


def run(
    *,
    region: str,
    stack_name: str,
    table_name: str,
    training_report: Path,
    output: Path,
    created_at: str | None = None,
) -> Dict[str, Any]:
    training = json.loads(training_report.read_text())
    if not isinstance(training, dict) or training.get("ok") is not True:
        raise RuntimeError("observational source training report is unavailable or unhealthy")
    records, table, s3, bucket, identity = prospective_runner._load_runtime_records(
        region=region,
        stack_name=stack_name,
        table_name=table_name,
    )
    observed_at = created_at or datetime.now(timezone.utc).isoformat()
    report = observational.advance(
        training=training,
        records=records,
        table=table,
        s3=s3,
        bucket=bucket,
        created_at=observed_at,
    )
    report.update(
        {
            "runnerVersion": VERSION,
            "runtimeIdentity": identity,
            "recordCountLoaded": len(records),
            "sourceTrainingReport": str(training_report),
            "sourceTrainingResultDigest": training.get("resultDigest"),
            "promotionEligible": False,
            "promotionRequested": False,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        }
    )
    report["reportDigest"] = observational._sha(
        {key: item for key, item in report.items() if key != "reportDigest"}
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--stack-name", default="parlay-platform-mlb-historical-optimizer"
    )
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--training-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        stack_name=args.stack_name,
        table_name=args.table_name,
        training_report=Path(args.training_report),
        output=Path(args.output),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": value.get("status"),
                "candidateDigest": value.get("candidateDigest"),
                "modelDigest": value.get("modelDigest"),
                "frozenCorpusLastDate": value.get("frozenCorpusLastDate"),
                "sampleSize": value.get("sampleSize"),
                "wins": value.get("wins"),
                "losses": value.get("losses"),
                "pushes": value.get("pushes"),
                "voids": value.get("voids"),
                "overallAccuracy": value.get("overallAccuracy"),
                "selectedPickAccuracy": value.get("selectedPickAccuracy"),
                "calibrationEce": value.get("calibrationEce"),
                "confidenceBands": value.get("confidenceBands"),
                "promotionEligible": value.get("promotionEligible"),
                "productionAuthorityChanged": value.get(
                    "productionAuthorityChanged"
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
