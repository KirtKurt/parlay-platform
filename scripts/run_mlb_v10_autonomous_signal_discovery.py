#!/usr/bin/env python3
"""Run V10 against every settled historical game currently available in AWS state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import mlb_historical_optimizer_v7_recovery_entrypoint as runtime
    import mlb_v10_autonomous_signal_discovery_v1 as v10

    handler = runtime.base.optimizer_handler
    state = handler._load_state()
    if not isinstance(state, dict):
        raise RuntimeError("historical optimizer state is missing")
    records = handler._load_training_records(state)
    report = v10.discover(records)
    report["state"] = {
        "phase": state.get("phase"),
        "eligibleGameCount": state.get("eligibleGameCount") or len(records),
        "completeSlateCount": state.get("completeSlateCount"),
        "currentDate": state.get("currentDate"),
        "currentSlotIndex": state.get("currentSlotIndex"),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "ok": True,
        "version": report.get("version"),
        "settledGameCount": report.get("settledGameCount"),
        "generatedPatternCount": report.get("generatedPatternCount"),
        "retainedPatternCount": report.get("retainedPatternCount"),
        "datasetFingerprint": report.get("datasetFingerprint"),
        "output": str(path),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
