#!/usr/bin/env python3
"""Run V10 against every settled historical game currently available in AWS state.

The runner intentionally imports the storage handler directly. It does not import the
V7/V8/V9 recovery entrypoint, so unrelated learner installation cannot prevent V10
from reading the immutable historical corpus and publishing discovery evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.output)
    started = datetime.now(timezone.utc)

    try:
        import mlb_historical_optimizer_handler as handler
        import mlb_v10_autonomous_signal_discovery_v1 as v10

        state = handler._load_state()
        if not isinstance(state, dict):
            raise RuntimeError("historical optimizer state is missing")
        records = handler._load_training_records(state)
        if not isinstance(records, list):
            records = list(records or [])
        if not records:
            raise RuntimeError("historical training corpus is empty")

        report = v10.discover(records)
        completed = datetime.now(timezone.utc)
        report.update(
            {
                "ok": True,
                "proofType": "MLB_V10_AUTONOMOUS_DISCOVERY_RUN",
                "sourceSha": os.environ.get("GITHUB_SHA"),
                "runId": os.environ.get("GITHUB_RUN_ID"),
                "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "startedAtUtc": started.isoformat(),
                "completedAtUtc": completed.isoformat(),
                "durationSeconds": round((completed - started).total_seconds(), 3),
                "blockers": [],
                "storageReader": "mlb_historical_optimizer_handler_direct",
            }
        )
        report["state"] = {
            "phase": state.get("phase"),
            "eligibleGameCount": state.get("eligibleGameCount") or len(records),
            "completeSlateCount": state.get("completeSlateCount"),
            "currentDate": state.get("currentDate"),
            "currentSlotIndex": state.get("currentSlotIndex"),
            "trainingRecordCountLoaded": len(records),
        }
        _write(path, report)
        print(
            json.dumps(
                {
                    "ok": True,
                    "version": report.get("version"),
                    "settledGameCount": report.get("settledGameCount"),
                    "generatedPatternCount": report.get("generatedPatternCount"),
                    "retainedPatternCount": report.get("retainedPatternCount"),
                    "datasetFingerprint": report.get("datasetFingerprint"),
                    "durationSeconds": report.get("durationSeconds"),
                    "output": str(path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        completed = datetime.now(timezone.utc)
        failure = {
            "ok": False,
            "proofType": "MLB_V10_AUTONOMOUS_DISCOVERY_RUN",
            "mode": "OUTCOME_ANCHORED_RETROSPECTIVE_DISCOVERY",
            "productionAuthority": False,
            "mayWriteChampion": False,
            "mayPublishPicks": False,
            "sourceSha": os.environ.get("GITHUB_SHA"),
            "runId": os.environ.get("GITHUB_RUN_ID"),
            "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "startedAtUtc": started.isoformat(),
            "completedAtUtc": completed.isoformat(),
            "durationSeconds": round((completed - started).total_seconds(), 3),
            "stalledStage": "V10_CORPUS_LOAD_OR_DISCOVERY",
            "errorType": type(exc).__name__,
            "error": str(exc),
            "tracebackTail": traceback.format_exc()[-12000:],
            "blockers": ["v10_discovery_execution_failed"],
        }
        _write(path, failure)
        print(json.dumps(failure, indent=2, sort_keys=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
